# Orchestrates extraction (Groq or rules), grouping, reconciliation, policy checks and recommendations.
# Produces separate email-level analyses and deal-level records, then writes JSON and the Markdown summary.
import json
import time
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from app.core import config
from app.core.config import Settings
from app.models.schemas import (
    Analysis,
    Assessment,
    Deal,
    EmailRecord,
    Evidence,
    Extraction,
    ReconciledTerms,
    Risk,
    SecuritySignal,
)
from app.services.ai_extractor import PROMPT_VERSION, EmailExtractor
from app.services.deal_grouper import group_emails
from app.services.email_parser import load_inputs
from app.services.recommendation import build_drafts_markdown, build_summary, recommend
from app.services.reply_generator import build_draft
from app.services.risk_analyzer import CreatorPolicy, analyze_risks
from app.services.rule_classifier import (
    decide_category,
    detect_security_signals,
    extract_with_rules,
    is_security_threat,
)
from app.services.term_reconciler import reconcile

_NON_DEALS = ("scam_or_phishing", "spam")


class PipelineResult(NamedTuple):
    analyses: list[Analysis]
    deals: list[Deal]
    run_info: dict


def assess(
    terms: ReconciledTerms,
    context: list[tuple[EmailRecord, Extraction]],
    signals: list[SecuritySignal],
    policy: CreatorPolicy,
    extra_risks: list[Risk] | None = None,
) -> Assessment:
    report = analyze_risks(terms, context, signals, policy)
    risks = report.risks + (extra_risks or [])
    category = decide_category(signals, report.is_collaboration, risks, report.missing_information)
    return Assessment(
        category=category,
        rate_check=report.rate_check,
        policy_checks=report.policy_checks,
        risks=risks,
        missing_information=[] if category in _NON_DEALS else report.missing_information,
        recommendation=recommend(category, risks, report.rate_check, report.missing_information),
    )


def _flat_fields(x: Extraction) -> dict:
    """Baseline-compatible flat fields, taken from this email's own extraction only."""
    timeline = x.timeline
    deadline = None
    if timeline:
        deadline = timeline.end_date.isoformat() if timeline.end_date else timeline.relative or timeline.raw_text
    contact = x.contact_email
    if x.contact_name:
        contact = f"{x.contact_name} <{x.contact_email}>"
    platforms = sorted({d.platform for d in x.deliverables if d.platform != "unknown"})
    return dict(
        brand=x.brand,
        contact=contact,
        platform=", ".join(platforms) or None,
        deliverables=[d.label() for d in x.deliverables],
        fee=x.compensation.amount if x.compensation else None,
        currency=x.compensation.currency if x.compensation else None,
        payment_type=x.payment_type,
        deadline=deadline,
        usage_rights=x.usage_rights.raw_text if x.usage_rights else None,
    )


def analyze_emails(
    emails: list[EmailRecord],
    profile: dict,
    settings: Settings | None = None,
    extractor: EmailExtractor | None = None,
) -> PipelineResult:
    settings = settings or config.settings
    ids = [e.id for e in emails]
    if len(ids) != len(set(ids)):
        raise ValueError("Inbox contains duplicate email ids.")
    policy = CreatorPolicy.from_profile(profile, settings.reference_date)
    extractor = extractor or EmailExtractor(settings)
    by_id = {e.id: e for e in emails}

    signals = {e.id: detect_security_signals(e) for e in emails}
    extractions = {}
    for email in emails:
        if settings.ai_skip_threats and extractor.clients and is_security_threat(signals[email.id]):
            # No model call for detected scams: saves tokens and keeps injection payloads away from the LLM.
            extractions[email.id] = extract_with_rules(email, fallback_reason="security_prefilter")
        else:
            extractions[email.id] = extractor.extract(email)
    groups = group_emails(emails, extractions, signals)

    analyses: dict[str, Analysis] = {}
    deals: list[Deal] = []
    for group in groups:
        members = [by_id[i] for i in group.email_ids]
        # Amounts in suspicious messages are never treated as deal terms; the raw reading stays in `extraction`.
        excluded = {e.id for e in members if is_security_threat(signals[e.id])}

        for index, email in enumerate(members):
            scope = members[: index + 1]
            terms = reconcile(scope, extractions, excluded)
            result = assess(terms, [(e, extractions[e.id]) for e in scope], signals[email.id], policy)
            x = extractions[email.id]
            analyses[email.id] = Analysis(
                email_id=email.id,
                received_at=email.received_at,
                sender=email.sender,
                subject=email.subject,
                category=result.category,
                deal_id=group.deal_id,
                deal_key=group.deal_id,
                is_latest_in_deal=index == len(members) - 1,
                extraction=x,
                security_signals=signals[email.id],
                terms_in_context=terms,
                rate_check=result.rate_check,
                policy_checks=result.policy_checks,
                risks=result.risks,
                missing_information=result.missing_information,
                recommendation_detail=result.recommendation,
                risk_flags=[f"{r.severity}: {r.title}" for r in result.risks],
                recommendation=result.recommendation.action,
                **_flat_fields(x),
            )

        deal_terms = reconcile(members, extractions, excluded)
        related_risks = [
            Risk(
                code="possible_related_deal", category="terms", severity="low",
                title="Possibly related to another deal",
                explanation=f"{link.email_id} / {link.other_email_id}: {link.reason}.",
                evidence=[Evidence(source="computed", quote=link.reason)],
                mitigation="Check whether these emails are about the same campaign before replying.",
            )
            for link in group.related
        ]
        deal_signals = [s for e in members for s in signals[e.id]]
        result = assess(deal_terms, [(e, extractions[e.id]) for e in members], deal_signals, policy, related_risks)
        deal = Deal(
            deal_id=group.deal_id,
            deal_key=group.deal_id,
            brand=deal_terms.brand,
            agency=deal_terms.agency,
            campaign=deal_terms.campaign,
            email_ids=group.email_ids,
            latest_email_id=group.email_ids[-1],
            first_received_at=members[0].received_at,
            last_received_at=members[-1].received_at,
            extraction_sources={e.id: extractions[e.id].source for e in members},
            grouping_evidence=group.evidence,
            possible_related=group.related,
            category=result.category,
            terms=deal_terms,
            security_signals=deal_signals,
            rate_check=result.rate_check,
            policy_checks=result.policy_checks,
            risks=result.risks,
            missing_information=result.missing_information,
            recommendation=result.recommendation,
        )
        # One draft per deal, addressed to the latest message; earlier emails in the thread get none.
        latest = members[-1]
        deal.draft_reply = build_draft(deal, latest, extractions[latest.id], policy)
        analyses[latest.id].draft_reply = deal.draft_reply.body if deal.draft_reply else None
        deals.append(deal)

    sources = Counter(x.source for x in extractions.values())
    clients = extractor.clients
    run_info = {
        "reference_date": policy.today.isoformat(),
        "reference_date_source": policy.today_source,
        "emails": len(emails),
        "deals": len(deals),
        "extraction_sources": dict(sources),
        "fallback_reasons": ", ".join(sorted({x.fallback_reason for x in extractions.values() if x.fallback_reason})),
        "ai_enabled": settings.ai_enabled,
        # Provider order = fallback order (Groq first, then Gemini), only those with a key configured.
        "ai_providers": [{"name": c.provider.name, "model": c.provider.model,
                          "response_format": c.provider.response_format} for c in clients],
        "prompt_version": PROMPT_VERSION,
        "ai_calls": {c.provider.name: c.calls for c in clients},
        "ai_disabled": getattr(extractor, "disabled_providers", {}),
        "ai_cache_hits": extractor.stats.get("cache_hits", 0),
        "ai_tokens": {c.provider.name: dict(c.usage) for c in clients if c.usage},
        "ai_avg_latency_seconds": {c.provider.name: round(sum(c.latencies) / len(c.latencies), 2)
                                   for c in clients if c.latencies},
    }
    ordered = [analyses[e.id] for e in emails]  # keep input order for email-level output
    return PipelineResult(ordered, deals, run_info)


def run(
    data_dir: Path,
    output_dir: Path,
    settings: Settings | None = None,
    extractor: EmailExtractor | None = None,
) -> tuple[list[Analysis], list[Deal]]:
    """Baseline-compatible entry point: returns (analyses, deals) and writes all outputs."""
    result = run_pipeline(data_dir, output_dir, settings, extractor)
    return result.analyses, result.deals


def run_pipeline(
    data_dir: Path,
    output_dir: Path,
    settings: Settings | None = None,
    extractor: EmailExtractor | None = None,
) -> PipelineResult:
    profile, emails = load_inputs(data_dir)
    started = time.perf_counter()
    result = analyze_emails(emails, profile, settings, extractor)
    elapsed = time.perf_counter() - started
    result.run_info["total_seconds"] = round(elapsed, 3)
    result.run_info["ms_per_email"] = round(1000 * elapsed / max(len(emails), 1), 1)
    output_dir.mkdir(parents=True, exist_ok=True)

    def dump(name: str, payload: object) -> None:
        (output_dir / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    dump("analyses.json", [a.model_dump(mode="json") for a in result.analyses])
    dump("deals.json", [d.model_dump(mode="json") for d in result.deals])
    dump("run_info.json", result.run_info)
    (output_dir / "summary.md").write_text(
        build_summary(result.deals, result.analyses, result.run_info), encoding="utf-8"
    )
    (output_dir / "drafts.md").write_text(build_drafts_markdown(result.deals), encoding="utf-8")
    return result
