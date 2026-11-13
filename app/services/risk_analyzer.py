# Deterministic creator-policy checks: security, content rules, exclusivity, rates, deadlines, usage rights.
# Reads the actual creator profile; rules it cannot interpret are reported as "unknown", never assumed.
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime

from app.core.config import LOCAL_TZ
from app.models.schemas import (
    EmailRecord,
    Evidence,
    Extraction,
    PolicyCheck,
    RateCheck,
    RateLine,
    ReconciledTerms,
    Risk,
    SecuritySignal,
)
from app.services.rule_classifier import CRITICAL_TERMS, is_security_threat
from app.services.term_reconciler import term_history_summary
from app.utils.helpers import (
    brand_keys_match,
    find_sentence,
    format_amount,
    format_money,
    is_freemail,
    sender_domain,
    truncate,
)

TIGHT_DEADLINE_DAYS = 3
SHORT_LEAD_DAYS = 3
HIGH_SHORTFALL_PCT = 50.0

# Generic domain vocabulary used to recognise profile categories in emails. Not tied to any sample email.
CATEGORY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "gambling": ("casino", "poker", "rummy", "teen patti", "satta", "lottery", "jackpot", "slot machine"),
    "betting": ("bet", "sportsbook", "wager"),
    "fantasy sports": ("fantasy cricket", "fantasy football", "fantasy league", "fantasy app", "fantasy team",
                       "fantasy game"),
    "crypto": ("cryptocurrency", "bitcoin", "ethereum", "nft", "web3", "altcoin", "crypto exchange", "memecoin"),
    "trading tip": ("stock tip", "intraday tip", "forex signal", "trading signal", "options tip", "f&o tip"),
    "alcohol": ("beer", "whisky", "whiskey", "vodka", "liquor", "wine", "rum"),
    "tobacco": ("cigarette", "vape", "e-cigarette", "hookah"),
    "smartphone": ("smart phone", "mobile phone", "handset", "5g phone", "android phone", "iphone"),
    "laptop": ("notebook computer", "ultrabook", "chromebook"),
    "earbud": ("earphone", "headphone", "tws"),
    "skincare": ("serum", "moisturiser", "moisturizer", "sunscreen", "spf", "face wash"),
}
_GENERIC_SUFFIXES = frozenset({"app", "apps", "promotion", "promotions", "product", "products", "brand", "brands",
                               "content", "ads", "advert", "adverts", "campaign", "campaigns"})
_DOC_REQUEST_RE = re.compile(r"\b(gst|pan|bank|invoice|contract|sign|aadh?aa?r|kyc|tax)\b", re.I)

SECURITY_MITIGATIONS = {
    "upfront_payment_request": "Never pay to join a campaign. Do not send money or reply; report and block the sender.",
    "platform_impersonation": "Do not use the email's link. Check the account only via the official app or website.",
    "credential_phishing": "Do not log in or 'verify' through the email; real platforms never ask this way.",
    "account_threat": "Check channel status directly in the official dashboard, not through this email.",
    "prompt_injection": "The embedded instructions were ignored. Treat the sender as untrusted.",
    "personal_data_request": "Never share a home address, personal phone number, passwords or OTPs.",
}


# ---------------------------------------------------------------- creator policy
@dataclass(frozen=True)
class ProhibitedRule:
    rule_text: str
    terms: tuple[str, ...]
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class ExclusivityCommitment:
    brand: str
    category: str | None
    until: date | None
    terms: str
    pattern: re.Pattern[str] | None

    def describe(self) -> str:
        until = self.until.isoformat() if self.until else "no end date"
        return f"{self.brand} exclusivity ({self.category or 'category not stated'}) until {until}: {self.terms}"


@dataclass
class CreatorPolicy:
    name: str
    niche: str | None
    platforms: frozenset[str]
    rate_currency: str
    base_rates: dict[str, float]
    fx_to_rate_currency: dict[str, float]
    accepted_currencies: frozenset[str] | None
    exclusivities: list[ExclusivityCommitment]
    prohibited: list[ProhibitedRule]
    pay_to_join_rule: str | None
    included_usage_months: float | None
    usage_policy_text: str | None
    today: date
    today_source: str
    unparsed_rules: list[str] = field(default_factory=list)

    @classmethod
    def from_profile(cls, profile: dict, override_today: date | None = None) -> "CreatorPolicy":
        if override_today:
            today, source = override_today, "REFERENCE_DATE setting"
        elif profile.get("today"):
            today, source = date.fromisoformat(str(profile["today"])), "creator profile 'today'"
        else:
            today, source = datetime.now(LOCAL_TZ).date(), "system clock (IST)"

        rate_key = next((k for k in profile if k.startswith("base_rates_")), None)
        rate_currency = rate_key.rsplit("_", 1)[-1].upper() if rate_key else "INR"
        base_rates = {k: float(v) for k, v in (profile.get(rate_key, {}) if rate_key else {}).items()}

        note = profile.get("currency_note", "") or ""
        fx = {rate_currency: 1.0}
        for src, rate, dst in re.findall(r"1\s*([A-Z]{3})\s*=\s*([\d.,]+)\s*([A-Z]{3})", note):
            if dst == rate_currency:
                fx[src] = float(rate.replace(",", ""))
        accepted_match = re.search(r"(?i:accepts?)\s+([A-Z]{3}(?:\s*(?:,|and|or|&)\s*[A-Z]{3})*)", note)
        accepted = frozenset(re.findall(r"[A-Z]{3}", accepted_match.group(1))) if accepted_match else None

        prohibited, unparsed, pay_rule = [], [], None
        for rule in profile.get("content_rules", []) or []:
            lower = rule.lower()
            if re.search(r"\bpays?\b", lower) and re.search(r"\b(join|programmes?|programs?|registration)\b", lower):
                pay_rule = rule
                continue
            m = re.match(r"\s*(?:no|never promote|avoid|do not promote)\s+(.+?)\.?\s*$", rule, re.I)
            if not m:
                unparsed.append(rule)
                continue
            terms = []
            for item in re.split(r",|\bor\b|\band\b|/", m.group(1), flags=re.I):
                words = [w for w in re.findall(r"[a-z0-9&-]+", item.lower()) if w not in _GENERIC_SUFFIXES]
                if words:
                    terms.append(" ".join(words).replace("-", " "))
            if terms:
                prohibited.append(ProhibitedRule(rule, tuple(terms), _keyword_pattern(_expand(terms))))
            else:
                unparsed.append(rule)

        exclusivities = []
        for item in profile.get("current_exclusivity", []) or []:
            category = item.get("category")
            until = date.fromisoformat(item["until"]) if item.get("until") else None
            pattern = _keyword_pattern(_expand([_singular(category)])) if category else None
            exclusivities.append(
                ExclusivityCommitment(item.get("brand", "unknown brand"), category, until, item.get("terms", ""), pattern)
            )

        usage_text = profile.get("usage_rights_policy")
        months = re.search(r"up to\s+(\d+(?:\.\d+)?)\s*months?", usage_text or "", re.I)
        return cls(
            name=profile.get("name", "the creator"),
            niche=profile.get("niche"),
            platforms=frozenset((profile.get("platforms") or {}).keys()),
            rate_currency=rate_currency,
            base_rates=base_rates,
            fx_to_rate_currency=fx,
            accepted_currencies=accepted,
            exclusivities=exclusivities,
            prohibited=prohibited,
            pay_to_join_rule=pay_rule,
            included_usage_months=float(months.group(1)) if months else None,
            usage_policy_text=usage_text,
            today=today,
            today_source=source,
            unparsed_rules=unparsed,
        )


def _singular(word: str) -> str:
    word = word.strip().lower()
    return word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith("ss") else word


def _expand(terms: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for term in terms:
        base = _singular(term)
        out.update({term, base, *CATEGORY_SYNONYMS.get(base, ()), *CATEGORY_SYNONYMS.get(term, ())})
    return {t for t in out if t}


def _keyword_pattern(terms: Iterable[str]) -> re.Pattern[str]:
    alternatives = sorted(
        {re.sub(r"(?:\\ |\\-|[ -])+", r"[\\s-]+", re.escape(t)) for t in terms}, key=len, reverse=True
    )
    return re.compile(r"\b(?:" + "|".join(alternatives) + r")s?\b", re.I)


# ---------------------------------------------------------------- report container
@dataclass
class RiskReport:
    is_collaboration: bool
    risks: list[Risk] = field(default_factory=list)
    policy_checks: list[PolicyCheck] = field(default_factory=list)
    rate_check: RateCheck | None = None
    missing_information: list[str] = field(default_factory=list)


def _check(report: RiskReport, name: str, status: str, detail: str) -> None:
    report.policy_checks.append(PolicyCheck(name=name, status=status, detail=detail))


def _quote(email_id: str | None, text: str) -> Evidence:
    return Evidence(source="email", email_id=email_id, quote=truncate(text, 240))


def _profile(text: str) -> Evidence:
    return Evidence(source="creator_profile", quote=truncate(text, 240))


# ---------------------------------------------------------------- rate calculation
def compute_rate_check(terms: ReconciledTerms, policy: CreatorPolicy) -> RateCheck:
    """Pure arithmetic against the profile's rate card. Never converts without a stated FX rate."""
    deliverables = terms.current("deliverables") or []
    comp = terms.current("compensation")
    payment_type = terms.current("payment_type")
    lines, unpriced = [], []
    for d in deliverables:
        rate = policy.base_rates.get(d.rate_key)
        if rate is None or d.quantity is None:
            unpriced.append(d.label())
            continue
        lines.append(RateLine(deliverable=d.label(), quantity=d.quantity, unit_rate_inr=rate, subtotal_inr=rate * d.quantity))
    minimum = sum(line.subtotal_inr for line in lines) if lines else None
    base = dict(lines=lines, unpriced=unpriced, minimum_inr=minimum)

    if not deliverables:
        return RateCheck(status="unavailable" if comp else "not_applicable", **base,
                         detail="No deliverables stated, so the offer cannot be compared with base rates.")
    if comp is None:
        if payment_type == "gifting":
            worth = f" Requested content is worth {format_amount(minimum, policy.rate_currency)} at base rates." if minimum else ""
            return RateCheck(status="not_applicable", **base, detail="Product-only (gifting) offer with no cash fee." + worth)
        return RateCheck(status="unavailable", **base, detail="No fee stated.")

    assumptions = []
    currency = comp.currency
    if currency is None:
        currency = policy.rate_currency
        assumptions.append(f"Currency not stated; compared as {currency} (the rate-card currency). Confirm with the brand.")
    fx = policy.fx_to_rate_currency.get(currency)
    common = dict(offered_amount=comp.amount, offered_currency=comp.currency, assumptions=assumptions, **base)
    if fx is None:
        return RateCheck(status="unavailable", **common,
                         detail=f"No {currency}->{policy.rate_currency} rate in the profile; comparison not possible.")
    offered = comp.amount * fx
    if fx != 1.0:
        assumptions.append(f"Converted at 1 {currency} = {fx:g} {policy.rate_currency} (from the profile).")
    common.update(fx_rate_to_inr=fx, offered_inr=offered)
    if minimum is None:
        return RateCheck(status="unavailable", **common, detail="None of the deliverables has a base rate in the profile.")
    if offered < minimum:
        shortfall = minimum - offered
        pct = round(100 * shortfall / minimum, 1)
        return RateCheck(status="below_rate", shortfall_inr=shortfall, shortfall_pct=pct, **common,
                         detail=f"Offer {format_amount(offered, 'INR')} is {format_amount(shortfall, 'INR')} ({pct}%) "
                                f"below the base rate of {format_amount(minimum, 'INR')}.")
    if unpriced:
        return RateCheck(status="unpriced_items", **common,
                         detail=f"Offer covers the priced items ({format_amount(minimum, 'INR')}); no base rate for: "
                                + ", ".join(unpriced) + ".")
    return RateCheck(status="meets_rate", **common,
                     detail=f"Offer {format_amount(offered, 'INR')} meets the base rate of {format_amount(minimum, 'INR')}.")


def missing_terms(terms: ReconciledTerms) -> list[str]:
    missing = []
    has_deliverables = bool(terms.current("deliverables"))
    paid = terms.current("compensation") is not None
    payment_type = terms.current("payment_type")
    if not has_deliverables:
        missing.append("deliverables")
    if not paid and payment_type not in ("gifting", "commission"):
        missing.append("compensation")
    if terms.current("timeline") is None:
        missing.append("timeline")
    if terms.current("usage_rights") is None and has_deliverables:
        missing.append("usage_rights")
    if terms.current("payment_terms") is None and paid:
        missing.append("payment_terms")
    return missing


# ---------------------------------------------------------------- main entry point
def analyze_risks(
    terms: ReconciledTerms,
    context: list[tuple[EmailRecord, Extraction]],
    signals: list[SecuritySignal],
    policy: CreatorPolicy,
) -> RiskReport:
    """`context` is every email in scope (this email plus earlier emails of the same deal)."""
    report = RiskReport(is_collaboration=any(x.is_brand_collaboration for _, x in context))
    threat = is_security_threat(signals)
    _security(report, signals, threat, policy)
    if not threat:
        _model_concerns(report, context)
    if threat or not report.is_collaboration:
        _check(report, "commercial_checks", "not_applicable",
               "Skipped: " + ("security threat; terms are not treated as a real offer." if threat
                              else "not a brand collaboration."))
        return report

    texts = [(e.id, f"{e.subject}\n{e.body}") for e, _ in context]
    descriptors = " | ".join(
        filter(None, (v for _, x in context for v in (x.product_category, x.product, x.campaign_name)))
    )
    _content_rules(report, texts, descriptors, policy)
    _platforms(report, terms, policy)
    _exclusivity(report, terms, texts, descriptors, policy)
    _deadline(report, terms, policy)
    _rates(report, terms, policy)
    _usage(report, terms, policy)
    _agency(report, context)
    _term_changes(report, terms)
    _missing(report, terms, context)
    _check(report, "niche", "unknown",
           f"Profile niche is '{policy.niche}'. Niche fit is a judgement call and is not scored automatically.")
    for rule in policy.unparsed_rules:
        _check(report, "unparsed_rule", "unknown", f"Rule not machine-checkable, review manually: {rule}")
    return report


def _security(report: RiskReport, signals: list[SecuritySignal], threat: bool, policy: CreatorPolicy) -> None:
    weak = [s for s in signals if s.strength == "weak"]
    if threat:
        for s in signals:
            evidence = [_quote(s.email_id, s.evidence)]
            explanation = s.explanation
            if s.code == "upfront_payment_request" and policy.pay_to_join_rule:
                explanation += f" Breaks the creator's rule: '{policy.pay_to_join_rule}'"
                evidence.append(_profile(policy.pay_to_join_rule))
            report.risks.append(Risk(
                code=s.code, category="security", severity="critical" if s.strength == "strong" else "low",
                title=s.code.replace("_", " ").capitalize(), explanation=explanation, evidence=evidence,
                mitigation=SECURITY_MITIGATIONS.get(s.code, "Treat as part of a likely scam; do not engage.")))
        _check(report, "security", "fail", "Plain-code scan found scam/phishing signals.")
        if policy.pay_to_join_rule:
            paid = any(s.code == "upfront_payment_request" for s in signals)
            _check(report, "never_pays_to_join", "fail" if paid else "pass", policy.pay_to_join_rule)
        return
    if len(weak) >= 2:
        report.risks.append(Risk(
            code="suspicious_indicators", category="security", severity="medium",
            title="Several suspicious indicators",
            explanation="; ".join(s.explanation for s in weak),
            evidence=[_quote(s.email_id, s.evidence) for s in weak],
            mitigation="Verify the sender through an independent channel before engaging."))
    _check(report, "security", "warning" if weak else "pass",
           "Weak signals only: " + ", ".join(s.code for s in weak) if weak else "No scam/phishing signals found.")
    if policy.pay_to_join_rule:
        _check(report, "never_pays_to_join", "pass", policy.pay_to_join_rule)


def _model_concerns(report: RiskReport, context: list[tuple[EmailRecord, Extraction]]) -> None:
    concerns = [(e.id, c) for e, x in context for c in x.security_concerns]
    concerns += [(e.id, "Model detected instructions aimed at an AI assistant.")
                 for e, x in context if x.prompt_injection_detected]
    if concerns:
        report.risks.append(Risk(
            code="ai_security_concern", category="security", severity="medium",
            title="AI extractor noted a possible security issue",
            explanation="; ".join(c for _, c in concerns) + " Plain-code checks found no definite scam signal.",
            evidence=[_quote(eid, c) for eid, c in concerns],
            mitigation="Review manually before replying; do not share personal or financial details."))


def _content_rules(report: RiskReport, texts: list[tuple[str, str]], descriptors: str, policy: CreatorPolicy) -> None:
    if not policy.prohibited:
        _check(report, "content_rules", "unknown", "No machine-readable content restrictions in the profile.")
        return
    broken = []
    for rule in policy.prohibited:
        for email_id, text in texts + [(None, descriptors)]:
            match = rule.pattern.search(text)
            if match:
                quote = find_sentence(text, re.escape(match.group())) or match.group()
                broken.append(rule.rule_text)
                report.risks.append(Risk(
                    code="prohibited_content", category="content_policy", severity="high", blocking=True,
                    title="Breaks a content rule",
                    explanation=f"Mentions '{match.group()}', which the creator's rule excludes: '{rule.rule_text}'",
                    evidence=[_quote(email_id, quote) if email_id else Evidence(source="computed", quote=quote),
                              _profile(rule.rule_text)],
                    term=f"category: {match.group()}",
                    mitigation="Decline politely; this category is excluded regardless of fee."))
                break
    if broken:
        _check(report, "content_rules", "fail", "Breaks: " + "; ".join(broken))
    else:
        _check(report, "content_rules", "pass", "No excluded category mentioned: "
               + "; ".join(r.rule_text for r in policy.prohibited))


def _platforms(report: RiskReport, terms: ReconciledTerms, policy: CreatorPolicy) -> None:
    deliverables = terms.current("deliverables") or []
    if not deliverables:
        _check(report, "platform", "unknown", "No deliverables stated.")
        return
    known = [d for d in deliverables if d.platform != "unknown"]
    unsupported = [d for d in known if d.platform not in policy.platforms]
    if known and len(unsupported) == len(deliverables):
        report.risks.append(Risk(
            code="platform_unsupported", category="platform", severity="high", blocking=True,
            title="Platform not used by the creator",
            explanation="Requested content is on platforms not in the creator profile: "
                        + ", ".join(d.label() for d in unsupported),
            evidence=[Evidence(source="computed", quote=", ".join(sorted(policy.platforms)))],
            mitigation="Decline or propose equivalent content on the creator's own platforms."))
        _check(report, "platform", "fail", "No requested platform is in the profile.")
    elif unsupported:
        report.risks.append(Risk(
            code="platform_partially_unsupported", category="platform", severity="medium",
            title="Some deliverables are on platforms the creator does not use",
            explanation=", ".join(d.label() for d in unsupported),
            mitigation="Ask to swap those items for Instagram/YouTube equivalents."))
        _check(report, "platform", "warning", "Some deliverables are outside the creator's platforms.")
    else:
        unknown = [d.label() for d in deliverables if d.platform == "unknown"]
        _check(report, "platform", "unknown" if unknown else "pass",
               ("Platform not stated for: " + ", ".join(unknown)) if unknown
               else "All deliverables are on " + ", ".join(sorted({d.platform for d in deliverables})) + ".")


def _exclusivity(report: RiskReport, terms: ReconciledTerms, texts: list[tuple[str, str]], descriptors: str,
                 policy: CreatorPolicy) -> None:
    for ex in policy.exclusivities:
        name = f"exclusivity:{ex.brand}"
        if ex.until and ex.until < policy.today:
            _check(report, name, "not_applicable", f"Expired on {ex.until.isoformat()}.")
            continue
        if brand_keys_match(terms.brand, ex.brand):
            _check(report, name, "pass", "Offer is from the exclusive partner itself.")
            continue
        if ex.pattern is None:
            _check(report, name, "unknown", "Exclusivity has no category, so competitors cannot be identified.")
            continue
        hit = next(((eid, m) for eid, text in texts + [(None, descriptors)]
                    if (m := ex.pattern.search(text))), None)
        if hit is None:
            _check(report, name, "pass", f"No {ex.category} product mentioned.")
            continue
        email_id, match = hit
        timeline = terms.current("timeline")
        campaign_date = (timeline.start_date or timeline.end_date) if timeline else None
        if campaign_date and ex.until and campaign_date > ex.until:
            _check(report, name, "pass", f"Campaign ({campaign_date}) starts after the exclusivity ends ({ex.until}).")
            continue
        when = (f"the campaign date ({campaign_date:%d %b %Y}) falls before {ex.until:%d %b %Y}"
                if campaign_date and ex.until else "no campaign date is given, so overlap must be assumed")
        source_text = next((t for eid, t in texts if eid == email_id), descriptors)
        report.risks.append(Risk(
            code="exclusivity_conflict", category="exclusivity", severity="high", blocking=True,
            title=f"Conflicts with {ex.brand} exclusivity",
            explanation=f"Promotes a {match.group()} from a different brand while the creator is exclusive to "
                        f"{ex.brand} for {ex.category}; {when}.",
            evidence=[_quote(email_id, find_sentence(source_text, ex.pattern) or match.group())
                      if email_id else Evidence(source="computed", quote=match.group()),
                      _profile(ex.describe())],
            term=f"product: {match.group()}",
            mitigation=f"Decline for now; the brand could be approached again after "
                       f"{ex.until:%d %b %Y}." if ex.until else "Decline unless the exclusivity terms allow it."))
        _check(report, name, "fail", f"Competing {ex.category} product during the exclusivity period.")

    requested = terms.current("exclusivity")
    if requested is not None:
        report.risks.append(Risk(
            code="exclusivity_requested", category="exclusivity", severity="medium",
            title="Brand asks for exclusivity",
            explanation=f"Requested: '{requested.raw_text}'. Scope and duration limit future deals.",
            evidence=[_quote(terms.exclusivity.source_email_id, requested.raw_text)],
            mitigation="Price exclusivity separately and limit its category and duration."))


def _deadline(report: RiskReport, terms: ReconciledTerms, policy: CreatorPolicy) -> None:
    timeline = terms.current("timeline")
    today = policy.today
    if timeline is None:
        _check(report, "deadline", "unknown", "No deadline stated.")
        return
    note = " (year not stated; inferred)" if timeline.year_assumed else ""
    source = terms.timeline.source_email_id
    end, start = timeline.end_date, timeline.start_date
    if end and end < today:
        days = (today - end).days
        report.risks.append(Risk(
            code="deadline_expired", category="deadline", severity="high", blocking=True,
            title="Deadline already passed",
            explanation=f"Content had to be live by {end:%d %b %Y}{note}, {days} day(s) before the reference date "
                        f"{today:%d %b %Y} ({policy.today_source}).",
            evidence=[_quote(source, timeline.raw_text)], term=f"deadline: {end.isoformat()}",
            mitigation="Decline, or reply only if the brand proposes a new date."))
        _check(report, "deadline", "fail", f"Deadline {end} is before {today}.")
    elif end and (end - today).days <= TIGHT_DEADLINE_DAYS:
        report.risks.append(Risk(
            code="deadline_tight", category="deadline", severity="medium", title="Very tight deadline",
            explanation=f"Due {end:%d %b %Y}{note}, only {(end - today).days} day(s) away.",
            evidence=[_quote(source, timeline.raw_text)], term=f"deadline: {end.isoformat()}",
            mitigation="Confirm production is feasible before agreeing, or ask for more time."))
        _check(report, "deadline", "warning", f"Due in {(end - today).days} day(s).")
    elif start and 0 <= (start - today).days <= SHORT_LEAD_DAYS:
        report.risks.append(Risk(
            code="short_lead_time", category="deadline", severity="low", title="Posting window opens soon",
            explanation=f"Posting window starts {start:%d %b %Y}{note}, {(start - today).days} day(s) from today.",
            evidence=[_quote(source, timeline.raw_text)], term=f"window: {start.isoformat()} to {end}",
            mitigation="Reply quickly so there is time to negotiate and produce the content."))
        _check(report, "deadline", "warning", f"Window opens in {(start - today).days} day(s).")
    else:
        detail = f"Due {end}{note}." if end else f"Relative timing: {timeline.relative or timeline.raw_text}."
        _check(report, "deadline", "pass", detail)


def _rates(report: RiskReport, terms: ReconciledTerms, policy: CreatorPolicy) -> None:
    rate = compute_rate_check(terms, policy)
    report.rate_check = rate
    status_map = {"meets_rate": "pass", "below_rate": "fail", "unpriced_items": "warning",
                  "unavailable": "unknown", "not_applicable": "not_applicable"}
    _check(report, "rate", status_map[rate.status], " ".join([rate.detail, *rate.assumptions]))
    comp = terms.current("compensation")
    comp_source = terms.compensation.source_email_id if terms.compensation else None

    if rate.status == "below_rate":
        items = ", ".join(line.deliverable for line in rate.lines)
        report.risks.append(Risk(
            code="below_rate", category="rate",
            severity="high" if (rate.shortfall_pct or 0) > HIGH_SHORTFALL_PCT else "medium",
            title="Below the creator's base rate",
            explanation=" ".join([
                f"{format_money(comp)} vs base rate {format_amount(rate.minimum_inr, 'INR')} for {items}:",
                f"short by {format_amount(rate.shortfall_inr, 'INR')} ({rate.shortfall_pct}%).",
                *rate.assumptions,
            ]),
            evidence=[_quote(comp_source, f"Offered {format_money(comp)}"),
                      *[_profile(f"{line.deliverable}: {format_amount(line.unit_rate_inr, 'INR')} each")
                        for line in rate.lines]],
            term=f"fee: {format_money(comp)}",
            mitigation=f"Counter at {format_amount(rate.minimum_inr, 'INR')} or more, or reduce the deliverables."))
    elif rate.status == "unavailable" and comp is not None and rate.offered_inr is None and rate.minimum_inr:
        report.risks.append(Risk(
            code="rate_comparison_unavailable", category="currency", severity="low",
            title="Cannot compare the fee with base rates", explanation=rate.detail,
            term=f"fee: {format_money(comp)}",
            mitigation="Convert manually at today's rate before deciding."))

    if comp is not None and comp.currency is None:
        report.risks.append(Risk(
            code="currency_unstated", category="currency", severity="low", title="Currency not stated",
            explanation=f"The fee {format_money(comp)} has no currency.",
            evidence=[_quote(comp_source, format_money(comp))], term=f"fee: {format_money(comp)}",
            mitigation="Confirm the currency in writing."))
        _check(report, "currency", "unknown", "Fee currency not stated.")
    elif comp is not None and policy.accepted_currencies is not None:
        ok = comp.currency in policy.accepted_currencies
        if not ok:
            report.risks.append(Risk(
                code="currency_not_accepted", category="currency", severity="medium",
                title="Currency the creator does not accept",
                explanation=f"Offer is in {comp.currency}; profile accepts {', '.join(sorted(policy.accepted_currencies))}.",
                term=f"fee: {format_money(comp)}", mitigation="Ask for payment in an accepted currency."))
        _check(report, "currency", "pass" if ok else "fail", f"Offer currency {comp.currency}.")

    payment_type = terms.current("payment_type")
    deliverables = terms.current("deliverables") or []
    if comp is None and payment_type == "gifting" and deliverables:
        value = terms.current("product_value")
        worth = f" (worth {format_amount(rate.minimum_inr, 'INR')} at base rates)" if rate.minimum_inr else ""
        report.risks.append(Risk(
            code="barter_only", category="compensation", severity="medium", title="Product only, no cash fee",
            explanation=f"Offers {'products worth ' + format_money(value) if value else 'products'} in exchange for "
                        f"{', '.join(d.label() for d in deliverables)}{worth}. Gifted products are not cash payment.",
            evidence=[_quote(terms.product_value.source_email_id if terms.product_value else None,
                             value and f"Product value {format_money(value)}" or "Gifted product")],
            term=f"payment: gifting{', product ' + format_money(value) if value else ''}",
            mitigation=(f"Ask for a cash fee (base value {format_amount(rate.minimum_inr, 'INR')})" if rate.minimum_inr
                        else "Ask for a cash fee") + ", or accept only if the product alone is worth the work."))
        _check(report, "barter_preference", "unknown", "Profile has no barter/gifting policy; creator must decide.")
    elif payment_type == "commission" and comp is None:
        report.risks.append(Risk(
            code="commission_only", category="compensation", severity="medium", title="Commission only, no fixed fee",
            explanation="Payment depends on sales; nothing is guaranteed.",
            mitigation="Ask for a fixed fee, with commission only as a bonus."))


def _usage(report: RiskReport, terms: ReconciledTerms, policy: CreatorPolicy) -> None:
    usage = terms.current("usage_rights")
    if usage is None:
        _check(report, "usage_rights", "unknown", "Usage rights not stated.")
        return
    source = terms.usage_rights.source_email_id
    included = policy.included_usage_months
    profile_ev = [_profile(policy.usage_policy_text)] if policy.usage_policy_text else []
    if usage.perpetual:
        report.risks.append(Risk(
            code="usage_perpetual", category="usage_rights", severity="high",
            title="Perpetual usage rights requested",
            explanation="Brand wants perpetual usage" + (" across all media" if usage.all_media else "")
                        + ". Creator policy: " + (policy.usage_policy_text or "not stated"),
            evidence=[_quote(source, usage.raw_text), *profile_ev], term="usage: perpetual",
            mitigation=(f"Limit paid usage to {included:g} months, or quote a separate fee for longer use."
                        if included else "Quote a separate fee for perpetual use or limit the duration.")))
        _check(report, "usage_rights", "fail", "Perpetual usage requested.")
    elif usage.duration_months is not None and included is not None and usage.duration_months > included:
        report.risks.append(Risk(
            code="usage_exceeds_included", category="usage_rights", severity="medium",
            title="Usage period longer than included",
            explanation=f"{usage.duration_months:g} months requested; {included:g} months are included in the rate.",
            evidence=[_quote(source, usage.raw_text), *profile_ev], term=f"usage: {usage.duration_months:g} months",
            mitigation=f"Charge extra for usage beyond {included:g} months."))
        _check(report, "usage_rights", "fail", "Usage exceeds the included period.")
    elif usage.paid_ads and usage.duration_months is None:
        report.risks.append(Risk(
            code="usage_duration_unspecified", category="usage_rights", severity="medium",
            title="Paid usage without a time limit",
            explanation="Paid-ad usage is requested but no duration is given.",
            evidence=[_quote(source, usage.raw_text)], term="usage: paid, duration unknown",
            mitigation=f"Agree a fixed duration (up to {included:g} months is included)." if included
            else "Agree a fixed duration."))
        _check(report, "usage_rights", "warning", "Paid usage with no duration.")
    else:
        within = f"within the {included:g} months included" if included and usage.duration_months else "as stated"
        _check(report, "usage_rights", "pass", f"Usage {within}: {usage.raw_text}")

    if usage.whitelisting:
        report.risks.append(Risk(
            code="whitelisting_requested", category="usage_rights", severity="medium",
            title="Whitelisting requested",
            explanation="Brand wants to run ads through the creator's handle. The profile has no whitelisting "
                        "policy, so this needs the creator's own decision.",
            evidence=[_quote(source, usage.raw_text)], term="usage: whitelisting",
            mitigation="Agree a separate whitelisting fee, a fixed duration and approval of ad copy."))
        _check(report, "whitelisting", "unknown", "Requested; profile does not cover whitelisting.")


def _agency(report: RiskReport, context: list[tuple[EmailRecord, Extraction]]) -> None:
    brand_side = sorted({sender_domain(e.sender) for e, x in context
                         if not x.agency and not is_freemail(sender_domain(e.sender))})
    seen = set()
    for email, x in context:
        domain = sender_domain(email.sender)
        if not x.agency or domain in brand_side or x.agency in seen:
            continue
        seen.add(x.agency)
        ask = find_sentence(email.body, _DOC_REQUEST_RE)
        origin = (f"Earlier brand emails came from {', '.join(brand_side)}." if brand_side
                  else "No email from the brand itself confirms this.")
        report.risks.append(Risk(
            code="agency_unverified", category="verification", severity="medium",
            title="Agency authority not verified",
            explanation=f"{x.agency} ({domain}) says it represents {x.brand or 'the brand'}. {origin}"
                        + (f" It asks: '{ask}'" if ask else ""),
            evidence=[_quote(email.id, ask or truncate(email.body, 160))],
            mitigation=f"Confirm with {x.brand or 'the brand'} directly"
                       + (f" ({brand_side[0]})" if brand_side else "")
                       + " that the agency is authorised before sharing GST, bank or tax details or signing."))


def _term_changes(report: RiskReport, terms: ReconciledTerms) -> None:
    unclear = [a for a in terms.ambiguities if "confirm which is final" in a]
    other = [a for a in terms.ambiguities if a not in unclear]
    if unclear:
        report.risks.append(Risk(
            code="terms_ambiguous", category="terms", severity="medium", title="Conflicting terms across emails",
            explanation=" ".join(unclear), mitigation="Ask the brand to confirm the final terms in one message."))
    if other:
        report.risks.append(Risk(
            code="terms_assumption", category="terms", severity="low", title="Assumption made while merging emails",
            explanation=" ".join(other), mitigation="Double-check these details with the brand."))
    comp = terms.compensation
    if comp and len(comp.history) > 1:
        amounts = [e.value.amount for e in comp.history]
        if comp.value.amount < max(amounts):
            report.risks.append(Risk(
                code="fee_reduced", category="terms", severity="low", title="Fee lowered during the thread",
                explanation="Fee history: " + (term_history_summary(terms, "compensation") or ""),
                evidence=[_quote(e.email_id, format_money(e.value)) for e in comp.history],
                term=f"fee: {format_money(comp.value)}",
                mitigation="Use the original offer as the anchor when negotiating."))


def _missing(report: RiskReport, terms: ReconciledTerms, context: list[tuple[EmailRecord, Extraction]]) -> None:
    report.missing_information = missing_terms(terms)
    critical = [m for m in report.missing_information if m in CRITICAL_TERMS]
    if critical:
        report.risks.append(Risk(
            code="missing_information", category="missing_information", severity="medium",
            title="Key deal details missing",
            explanation="Not stated in the email(s): " + ", ".join(m.replace("_", " ") for m in critical) + ".",
            mitigation="Ask for deliverables, fee, timeline and usage rights before committing."))
    attachments = [e.id for e, x in context if x.mentions_attachment]
    if attachments:
        report.risks.append(Risk(
            code="attachment_not_reviewed", category="missing_information", severity="low",
            title="Details are in an attachment that was not read",
            explanation="The email points to an attachment; attachments are not opened or analysed by this tool.",
            evidence=[_quote(attachments[0], "Attachment referenced")],
            mitigation="Ask for the key terms in the email body; open attachments only from verified senders."))
    payment_terms = terms.current("payment_terms")
    _check(report, "payment_terms", "pass" if payment_terms else "unknown",
           payment_terms or "Payment schedule not stated.")
