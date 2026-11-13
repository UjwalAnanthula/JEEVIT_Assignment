# Turns category + deterministic risks into an explicit next action, and writes the creator's Markdown summary.
# The model never chooses the action: security and creator-rule outcomes are fixed by plain code.
from collections import Counter

from app.models.schemas import Action, Analysis, Category, Deal, RateCheck, ReconciledTerms, Recommendation, Risk
from app.utils.helpers import format_money

NEGOTIATION_CODES = frozenset({
    "below_rate", "barter_only", "commission_only", "usage_perpetual", "usage_exceeds_included",
    "whitelisting_requested", "usage_duration_unspecified", "exclusivity_requested", "currency_not_accepted",
})
REVIEW_CODES = frozenset({"ai_security_concern", "suspicious_indicators", "terms_ambiguous",
                          "platform_partially_unsupported"})
VERIFY_CODES = frozenset({"agency_unverified"})
CONFIRM_CODES = frozenset({"currency_unstated", "short_lead_time", "deadline_tight", "terms_assumption"})
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_TERM_LABELS = {"deliverables": "deliverables", "compensation": "fee", "timeline": "timeline/deadline",
                "usage_rights": "usage rights", "payment_terms": "payment terms"}


def _sorted(risks: list[Risk]) -> list[Risk]:
    return sorted(risks, key=lambda r: _SEVERITY_ORDER[r.severity])


def _lc(text: str) -> str:
    """Lower-case only the first letter so brand names keep their capitals."""
    return text[:1].lower() + text[1:]


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def recommend(
    category: Category,
    risks: list[Risk],
    rate_check: RateCheck | None,
    missing: list[str],
) -> Recommendation:
    risks = _sorted(risks)
    if category == "scam_or_phishing":
        strong = [r for r in risks if r.severity == "critical"] or risks
        return Recommendation(
            action="flag_for_security_review",
            summary="Likely scam or phishing. Do not reply, pay, click links or share any details.",
            reasons=[r.explanation for r in strong],
            next_steps=_unique([r.mitigation for r in strong] + ["Report the sender as spam/phishing and block it."]),
        )
    if category == "spam":
        return Recommendation(action="ignore", summary="Not a brand offer (newsletter or bulk mail). No action needed.",
                              reasons=["No collaboration request, deliverables or fee in the email."])
    if category == "not_a_fit":
        blocking = [r for r in risks if r.blocking]
        return Recommendation(
            action="decline",
            summary="Decline: " + "; ".join(_lc(r.title) for r in blocking) + ".",
            reasons=[r.explanation for r in blocking],
            next_steps=_unique([r.mitigation for r in blocking] + ["Send a short, polite decline."]),
        )
    ask_for = [_TERM_LABELS.get(m, m) for m in missing]
    if category == "needs_more_info":
        steps = [f"Ask for: {', '.join(ask_for)}."]
        steps += [r.mitigation for r in risks if r.code in ("attachment_not_reviewed", *VERIFY_CODES)]
        return Recommendation(
            action="request_more_information",
            summary="Possibly real, but key details are missing.",
            reasons=[r.explanation for r in risks if r.category == "missing_information"]
            or [f"Missing: {', '.join(ask_for)}."],
            next_steps=_unique(steps),
        )

    negotiate = [r for r in risks if r.code in NEGOTIATION_CODES]
    review = [r for r in risks if r.code in REVIEW_CODES]
    verify = [r for r in risks if r.code in VERIFY_CODES]
    confirm = [r for r in risks if r.code in CONFIRM_CODES]
    follow_ups = [r.mitigation for r in verify + confirm]
    if ask_for:
        follow_ups.append(f"Confirm in writing: {', '.join(ask_for)}.")

    action: Action
    if negotiate:
        action = "negotiate"
        summary = "Worth pursuing, but negotiate: " + "; ".join(_lc(r.title) for r in negotiate) + "."
        reasons = [r.explanation for r in negotiate + verify]
        steps = [r.mitigation for r in negotiate] + follow_ups
    elif review:
        action = "review_manually"
        summary = "Looks like a real offer, but something needs a human check first."
        reasons = [r.explanation for r in review]
        steps = [r.mitigation for r in review] + follow_ups
    else:
        action = "accept"
        meets = rate_check.detail if rate_check and rate_check.status == "meets_rate" else "No rule or rate issue found."
        summary = "Good fit: terms meet the creator's rates and rules. Accept once details are confirmed."
        reasons = [meets] + [r.explanation for r in verify]
        steps = follow_ups or ["Reply to accept and request the contract."]
    return Recommendation(action=action, summary=summary, reasons=_unique(reasons), next_steps=_unique(steps))


def placeholder_reply(action: Action) -> str | None:
    """Baseline placeholder kept until Batch 3 adds contextual drafts. Nothing is ever sent."""
    if action == "request_more_information":
        return "Thanks for reaching out. Please share the complete brief, timeline, deliverables and usage terms."
    if action in ("accept", "negotiate", "review_manually"):
        return "Thanks for the offer. Could you confirm the final scope, payment schedule and usage rights?"
    return None


# ---------------------------------------------------------------- creator summary
def _terms_line(terms: ReconciledTerms) -> str:
    parts = []
    comp = terms.current("compensation")
    if comp:
        parts.append(format_money(comp))
    elif terms.current("payment_type") == "gifting":
        value = terms.current("product_value")
        parts.append(f"gifted product{' worth ' + format_money(value) if value else ''}")
    deliverables = terms.current("deliverables")
    if deliverables:
        parts.append(", ".join(d.label() for d in deliverables))
    timeline = terms.current("timeline")
    if timeline and (timeline.start_date or timeline.end_date):
        if timeline.start_date and timeline.end_date:
            parts.append(f"{timeline.start_date:%d %b}-{timeline.end_date:%d %b %Y}")
        else:
            parts.append(f"due {(timeline.end_date or timeline.start_date):%d %b %Y}")
    elif timeline:
        parts.append(timeline.relative or timeline.raw_text)
    return " | ".join(parts) if parts else "no concrete terms"


def _deal_block(deal: Deal) -> list[str]:
    rec = deal.recommendation
    title = deal.brand or "Unknown sender"
    if deal.agency:
        title += f" (via {deal.agency})"
    emails = ", ".join(deal.email_ids)
    lines = [f"### {title} [{emails}]", f"**Next step: {rec.action.replace('_', ' ')}.** {rec.summary}"]
    if deal.category not in ("scam_or_phishing", "spam"):
        lines.append(f"- Latest terms: {_terms_line(deal.terms)}")
        if deal.rate_check and deal.rate_check.offered_inr is not None and deal.rate_check.minimum_inr:
            lines.append(f"- Rate check: {deal.rate_check.detail}")
    shown = [r for r in _sorted(deal.risks) if r.severity in ("critical", "high", "medium")][:4]
    for risk in shown:
        lines.append(f"- {risk.severity.upper()}: {risk.title}. {risk.explanation}")
    for step in rec.next_steps[:4]:
        lines.append(f"- To do: {step}")
    return lines + [""]


def build_summary(deals: list[Deal], analyses: list[Analysis], run_info: dict) -> str:
    by_action = Counter(d.recommendation.action for d in deals)
    sources = Counter(a.extraction.source for a in analyses if a.extraction)
    lines = [
        "# Brand inbox summary",
        "",
        f"{len(analyses)} emails, grouped into {len(deals)} deals. Reference date: {run_info['reference_date']} "
        f"({run_info['reference_date_source']}).",
        f"Extraction: {', '.join(f'{n} by {s}' for s, n in sources.items())}"
        + (f" (fallback reason: {run_info['fallback_reasons']})" if run_info.get("fallback_reasons") else "")
        + ". Rates, rules, deadlines and security are always checked by plain code.",
        "",
    ]
    sections: list[tuple[str, tuple[str, ...]]] = [
        ("Needs your attention today", ("accept", "negotiate", "review_manually", "request_more_information")),
        ("Decline politely", ("decline",)),
        ("Security warnings: do not engage", ("flag_for_security_review",)),
    ]
    urgency = {"accept": 0, "negotiate": 1, "review_manually": 2, "request_more_information": 3}

    def sort_key(deal: Deal) -> tuple:
        timeline = deal.terms.current("timeline")
        due = (timeline.start_date or timeline.end_date) if timeline else None
        return (urgency.get(deal.recommendation.action, 9), due is None, due or deal.first_received_at)

    for heading, actions in sections:
        chosen = sorted((d for d in deals if d.recommendation.action in actions), key=sort_key)
        if not chosen:
            continue
        lines += [f"## {heading} ({len(chosen)})", ""]
        for deal in chosen:
            lines += _deal_block(deal)
    ignored = [d for d in deals if d.recommendation.action == "ignore"]
    if ignored:
        lines += [f"## Ignored ({len(ignored)})", ""]
        lines += [f"- {d.brand or 'Unknown'} [{', '.join(d.email_ids)}]: {d.recommendation.summary}" for d in ignored]
        lines.append("")
    lines.append("_Counts by next step: " + ", ".join(f"{a.replace('_', ' ')}: {n}" for a, n in by_action.items())
                 + ". Nothing has been sent; all replies are drafts for you to review._")
    return "\n".join(lines) + "\n"

