# Groups emails into deals using thread headers, subjects, brand identity and campaign/product evidence.
# Every merge records its reason; ambiguous or conflicting links stay separate and are flagged for review.
import re
from dataclasses import dataclass, field
from typing import Literal

from app.models.schemas import EmailRecord, Extraction, GroupingEvidence, RelatedLink, SecuritySignal
from app.services.rule_classifier import is_security_threat, strip_reply_prefix
from app.utils.helpers import (
    brand_keys_match,
    domain_label,
    is_freemail,
    sender_address,
    sender_domain,
    slugify,
)

LinkKind = Literal["duplicate", "strong", "medium", "conflict", "ambiguous"]
_MERGE_RANK = {"duplicate": 3, "strong": 2, "medium": 1}
_STOPWORDS = frozenset(
    "a an the our your new latest for of and or to in on with launch launching campaign brand product "
    "collab collaboration offer deal promo promotion range series edition".split()
)
_GENERIC_SUBJECT = frozenset(
    "re fw fwd collab collaboration partnership hi hello hey opportunity proposal urgent quick question "
    "inquiry enquiry brand deal paid query".split()
)
_FOLLOW_UP_ROLES = frozenset({"follow_up", "revision", "confirmation", "counter_offer"})


@dataclass
class DealGroup:
    deal_id: str
    email_ids: list[str]  # chronological (received time, then id)
    evidence: list[GroupingEvidence] = field(default_factory=list)
    related: list[RelatedLink] = field(default_factory=list)


@dataclass
class _Member:
    email: EmailRecord
    extraction: Extraction
    groupable: bool


def campaign_tokens(extraction: Extraction) -> set[str]:
    words = re.findall(r"[a-z0-9]+", " ".join(filter(None, [extraction.campaign_name, extraction.product])).lower())
    brand_words = set(re.findall(r"[a-z0-9]+", (extraction.brand or "").lower()))
    return {w for w in words if len(w) > 1 and w not in _STOPWORDS and w not in brand_words}


def subject_key(email: EmailRecord) -> str:
    return " ".join(re.findall(r"[a-z0-9&]+", strip_reply_prefix(email.subject).lower()))


def _specific_subject(key: str) -> bool:
    return len([w for w in key.split() if w not in _GENERIC_SUBJECT]) >= 2


def _same_sender_org(a: EmailRecord, b: EmailRecord) -> bool:
    da, db = sender_domain(a.sender), sender_domain(b.sender)
    if is_freemail(da) or is_freemail(db):
        return sender_address(a.sender) == sender_address(b.sender)
    return bool(da) and da == db


def _body_key(email: EmailRecord) -> str:
    return " ".join(email.body.lower().split())


def _link(new: _Member, old: _Member) -> tuple[LinkKind, str] | None:
    """Evidence that `new` belongs to the same deal as the earlier `old` email."""
    e, o = new.email, old.email
    if e.thread_id and e.thread_id == o.thread_id:
        return "strong", f"same thread id ({e.thread_id})"
    if o.message_id and (e.in_reply_to == o.message_id or o.message_id in e.references):
        return "strong", f"reply headers reference {o.id}"
    if (
        sender_address(e.sender) == sender_address(o.sender)
        and subject_key(e) == subject_key(o)
        and _body_key(e) == _body_key(o)
    ):
        return "duplicate", f"duplicate of {o.id} (same sender, subject and body)"
    if not (new.groupable and old.groupable):
        return None  # scams/spam only join a deal on hard header evidence

    x, ox = new.extraction, old.extraction
    same_brand = brand_keys_match(x.brand, ox.brand)
    skey = subject_key(e)
    if skey == subject_key(o) and _specific_subject(skey) and (_same_sender_org(e, o) or same_brand):
        return "strong", f"same subject thread '{strip_reply_prefix(e.subject)}'"
    if not same_brand:
        return None

    tokens, other_tokens = campaign_tokens(x), campaign_tokens(ox)
    brand = x.brand or ox.brand
    if tokens and other_tokens:
        shared = tokens & other_tokens
        if shared:
            return "strong", f"same brand ({brand}) and campaign/product ({', '.join(sorted(shared))})"
        return "conflict", (
            f"same brand ({brand}) but different campaigns/products "
            f"({', '.join(sorted(tokens))} vs {', '.join(sorted(other_tokens))})"
        )
    if x.references_prior_message or x.message_role in _FOLLOW_UP_ROLES:
        how = "an agency writing for the brand" if x.agency else "the brand"
        return "medium", f"same brand ({brand}); follow-up from {how} that refers to earlier messages"
    return "ambiguous", f"same brand ({brand}) but no campaign or thread evidence links the messages"


def _deal_id(members: list[_Member]) -> str:
    root = members[0]
    name = next((m.extraction.brand for m in members if m.extraction.brand), None)
    if not name:
        domain = sender_domain(root.email.sender)
        name = None if is_freemail(domain) else domain_label(domain)
    return f"deal-{slugify(name)}-{slugify(root.email.id)}"


def group_emails(
    emails: list[EmailRecord],
    extractions: dict[str, Extraction],
    signals: dict[str, list[SecuritySignal]],
) -> list[DealGroup]:
    ordered = sorted(emails, key=lambda e: (e.received_dt, e.id))
    groups: list[list[_Member]] = []
    evidence: list[list[GroupingEvidence]] = []
    related: list[list[RelatedLink]] = []

    for email in ordered:
        x = extractions[email.id]
        member = _Member(email, x, x.is_brand_collaboration and not is_security_threat(signals.get(email.id, [])))

        merge_links: dict[int, list[tuple[LinkKind, str, str]]] = {}
        soft_links: list[tuple[int, str, str]] = []  # conflict/ambiguous: (group, other id, reason)
        for gi, group in enumerate(groups):
            for old in group:
                found = _link(member, old)
                if found is None:
                    continue
                kind, reason = found
                if kind in _MERGE_RANK:
                    merge_links.setdefault(gi, []).append((kind, reason, old.email.id))
                else:
                    soft_links.append((gi, old.email.id, reason))

        target: int | None = None
        if merge_links:
            best = {gi: max(_MERGE_RANK[k] for k, _, _ in links) for gi, links in merge_links.items()}
            top = max(best.values())
            winners = [gi for gi, rank in best.items() if rank == top]
            if len(winners) == 1:
                target = winners[0]
            else:
                for gi in winners:
                    for _, reason, other in merge_links[gi]:
                        soft_links.append((gi, other, f"matches more than one deal ({reason}); kept separate"))

        if target is None:
            groups.append([member])
            evidence.append([])
            related.append([])
            new_index = len(groups) - 1
            for gi, other, reason in soft_links:
                link = RelatedLink(email_id=email.id, other_email_id=other, reason=reason)
                related[new_index].append(link)
                related[gi].append(link)
            continue

        groups[target].append(member)
        for kind, reason, other in sorted(merge_links[target], key=lambda t: -_MERGE_RANK[t[0]]):
            evidence[target].append(
                GroupingEvidence(email_id=email.id, linked_email_id=other, reason=reason, strength=kind)
            )
        for gi, other, reason in soft_links:
            if gi != target:
                link = RelatedLink(email_id=email.id, other_email_id=other, reason=reason)
                related[target].append(link)
                related[gi].append(link)

    result = []
    for members, ev, rel in zip(groups, evidence, related):
        result.append(
            DealGroup(
                deal_id=_deal_id(members),
                email_ids=[m.email.id for m in members],
                evidence=ev,
                related=rel,
            )
        )
    return result
