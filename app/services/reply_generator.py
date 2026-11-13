# Builds short draft replies from reconciled terms, risk codes and the creator profile. Nothing is ever sent.
# Drafts only restate facts found in the emails or the profile; anything missing becomes a question.
from app.models.schemas import Deal, Deliverable, DraftReply, EmailRecord, Extraction, Risk
from app.services.risk_analyzer import CreatorPolicy
from app.services.rule_classifier import strip_reply_prefix
from app.utils.helpers import format_amount, format_money, sender_address

NO_REPLY_ACTIONS = frozenset({"ignore", "flag_for_security_review"})
_ASK_LABELS = {
    "deliverables": "the deliverables (platform and number of posts)",
    "compensation": "the budget/fee",
    "timeline": "the timeline and go-live dates",
    "usage_rights": "usage rights (where and for how long the content will be used)",
    "payment_terms": "payment terms (advance, invoice timing)",
}


def prose_deliverables(items: list[Deliverable]) -> str:
    """'2 Instagram reels and 3 Instagram stories' rather than '2 x Instagram reel, 3 x Instagram story'."""
    plurals = {"story": "stories", "dedicated_video": "dedicated videos"}
    names = {"instagram": "Instagram ", "youtube": "YouTube "}
    parts = []
    for d in items:
        noun = d.type.replace("_", " ")
        if d.quantity and d.quantity > 1:
            noun = plurals.get(d.type, noun + "s")
        prefix = f"{d.quantity} " if d.quantity else ""
        parts.append(f"{prefix}{names.get(d.platform, '')}{noun}")
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _first_name(policy: CreatorPolicy) -> str:
    return (policy.name or "").split()[0] if policy.name else ""


def _greeting(x: Extraction, brand: str | None) -> str:
    if x.contact_name:
        return f"Hi {x.contact_name},"
    return f"Hi {brand} team," if brand else "Hi,"


def _subject(email: EmailRecord) -> str:
    return email.subject if email.subject.lower().startswith("re:") else f"Re: {strip_reply_prefix(email.subject)}"


def _terms_bullets(deal: Deal) -> list[str]:
    t = deal.terms
    lines = []
    if t.current("deliverables"):
        lines.append("Deliverables: " + ", ".join(d.label() for d in t.current("deliverables")))
    if t.current("compensation"):
        lines.append("Fee: " + format_money(t.current("compensation")))
    timeline = t.current("timeline")
    if timeline:
        lines.append("Timeline: " + timeline.raw_text)
    usage = t.current("usage_rights")
    if usage and usage.raw_text:
        lines.append("Usage: " + usage.raw_text)
    return [f"- {line}" for line in lines]


def _negotiation_points(deal: Deal, policy: CreatorPolicy) -> list[str]:
    risks: dict[str, Risk] = {r.code: r for r in deal.risks}
    rate = deal.rate_check
    t = deal.terms
    included = policy.included_usage_months
    deliverables = prose_deliverables(t.current("deliverables") or []) or "this scope"
    points = []
    if "below_rate" in risks and rate and rate.minimum_inr:
        point = f"My rate for {deliverables} is {format_amount(rate.minimum_inr, 'INR')}."
        comp = t.compensation
        if comp and len(comp.history) > 1 and max(e.value.amount for e in comp.history) > comp.value.amount:
            first = comp.history[0].value
            point += f" Your original offer of {format_money(first)} worked for me; could we go back to that?"
        else:
            point += f" Could you do {format_amount(rate.minimum_inr, 'INR')}?"
        points.append(point)
    if "barter_only" in risks:
        fee = f" my fee for {deliverables} is {format_amount(rate.minimum_inr, 'INR')}" if rate and rate.minimum_inr else ""
        points.append(f"I work on paid collaborations;{fee}. Happy to include the product as well.")
    if "commission_only" in risks:
        points.append("I'd need a fixed fee for this; commission can sit on top as a bonus.")
    if "usage_perpetual" in risks:
        keep = f"keep paid usage to {included:g} months (included in my fee)" if included else "limit the usage period"
        points.append(f"Perpetual or all-media usage is priced separately. We could {keep}, "
                      "or I can quote for longer usage.")
    if "usage_exceeds_included" in risks and included:
        points.append(f"My fee includes {included:g} months of usage; anything longer is an additional fee.")
    if "usage_duration_unspecified" in risks:
        cap = f" My fee includes up to {included:g} months." if included else ""
        points.append(f"How long would you like to use the content for paid ads?{cap}")
    if "whitelisting_requested" in risks:
        points.append("Whitelisting (running ads from my handle) is a separate fee, for a fixed period, "
                      "with my approval of the ad copy.")
    if "exclusivity_requested" in risks:
        points.append("Could you share the exact scope and duration of the exclusivity? It is priced separately.")
    if "currency_not_accepted" in risks and policy.accepted_currencies:
        points.append(f"Could we do the fee in {' or '.join(sorted(policy.accepted_currencies))}?")
    if "currency_unstated" in risks:
        points.append("Could you confirm the currency of the budget?")
    return points


def _verification_point(deal: Deal, policy: CreatorPolicy) -> str | None:
    if not any(r.code == "agency_unverified" for r in deal.risks):
        return None
    brand = deal.brand or "the brand"
    return (f"Before I share GST or other business details, could you loop in someone from the {brand} team "
            "on this thread to confirm the agency arrangement?")


def _decline_body(deal: Deal, policy: CreatorPolicy) -> str:
    codes = {r.code for r in deal.risks if r.blocking}
    if "exclusivity_conflict" in codes:
        until = next((ex.until for ex in policy.exclusivities if ex.until and ex.until >= policy.today), None)
        when = f" until {until:%B %Y}" if until else ""
        return ("Thank you for thinking of me. I have an existing category exclusivity" + when
                + ", so I can't take this on right now. I'd be glad to reconnect after that.")
    if "prohibited_content" in codes:
        return ("Thank you for the offer. This category isn't something I promote on my channels, "
                "so I'll pass on this one.")
    if "deadline_expired" in codes:
        timeline = deal.terms.current("timeline")
        date = f" ({timeline.end_date.day} {timeline.end_date:%B})" if timeline and timeline.end_date else ""
        return (f"Thanks for reaching out. The go-live date{date} has already passed, so I can't take this on. "
                "Happy to hear about future campaigns.")
    if "platform_unsupported" in codes:
        return "Thanks for reaching out. I don't create content on that platform, so I'll pass on this one."
    return "Thank you for the offer. It isn't the right fit for me at the moment, but I appreciate you reaching out."


def build_draft(deal: Deal, latest: EmailRecord, latest_x: Extraction, policy: CreatorPolicy) -> DraftReply | None:
    action = deal.recommendation.action
    if action in NO_REPLY_ACTIONS:
        return None
    greeting = _greeting(latest_x, deal.brand)
    sign = f"\n\nBest,\n{_first_name(policy)}".rstrip()
    about = f" about the {deal.campaign} campaign" if deal.campaign else ""
    asks = [_ASK_LABELS.get(m, m.replace("_", " ")) for m in deal.missing_information]
    notes = ["Draft only: review and edit before sending. Nothing has been sent."]

    if action == "decline":
        body = _decline_body(deal, policy)
    elif action == "request_more_information":
        lines = [f"Thanks for reaching out{about}! I'd love to learn more. Could you share:"]
        lines += [f"- {a}" for a in asks] or ["- the full brief"]
        if any(r.code == "attachment_not_reviewed" for r in deal.risks):
            lines.append("I'm not able to open attachments, so please include the key details in the email itself.")
        body = "\n".join(lines)
    elif action == "negotiate":
        points = _negotiation_points(deal, policy)
        verify = _verification_point(deal, policy)
        lines = [f"Thanks for the details{about}. I'm interested; a few points before we confirm:"]
        lines += [f"- {p}" for p in points + ([verify] if verify else [])]
        if asks:
            lines.append("Could you also confirm " + ", ".join(asks) + "?")
        lines.append("Looking forward to making this work.")
        body = "\n".join(lines)
        if any(r.code == "below_rate" for r in deal.risks):
            notes.append("Check the counter amount is what you want to ask for.")
    elif action == "accept":
        lines = [f"Thanks for reaching out{about}. I'd be happy to go ahead on these terms:", *_terms_bullets(deal)]
        lines.append("Could you confirm " + ", ".join(asks) + " and share the contract?" if asks
                     else "Please share the contract and I'll confirm.")
        body = "\n".join(lines)
    else:  # review_manually
        body = ("Thanks for your email. Before we go further, could you confirm the full terms in one message: "
                "deliverables, fee, timeline, usage rights and payment terms?")
        notes.append("Flagged for manual review: read the risks before replying.")

    if latest_x.agency:
        notes.append(f"You are replying to the agency ({latest_x.agency}), not {deal.brand or 'the brand'} directly.")
    return DraftReply(to=sender_address(latest.sender), subject=_subject(latest), body=f"{greeting}\n\n{body}{sign}",
                      notes=notes)
