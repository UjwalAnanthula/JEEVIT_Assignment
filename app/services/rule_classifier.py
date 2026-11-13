# Deterministic extraction (the no-AI fallback), security-signal detection and the final category rule.
# Security signals and the category decision always run in plain code, whatever produced the extraction.
import re

from app.models.schemas import (
    Category,
    Deliverable,
    EmailRecord,
    ExclusivityTerm,
    Extraction,
    MessageRole,
    Money,
    PaymentType,
    Risk,
    SecuritySignal,
    UsageRights,
)
from app.utils.helpers import (
    domain_label,
    find_money,
    find_sentence,
    is_freemail,
    normalize_key,
    parse_timeline,
    sender_address,
    sender_domain,
    sentence_spans,
    split_sentences,
    truncate,
)

SCAM_WEAK_SIGNAL_THRESHOLD = 3
CRITICAL_TERMS = ("deliverables", "compensation")

# ---------------------------------------------------------------- brand and contact
_NAME_TOKEN = r"(?:[A-Z0-9][\w'\u2019]*|&)"
_NAME = rf"{_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{0,3}}"
_REPRESENTED_RE = re.compile(
    rf"(?i:\bon behalf of|\brepresenting|\b(?:campaigns?|partnerships?|influencer marketing) for)"
    rf"\s+(?P<name>{_NAME})"
)
_SELF_INTRO_RE = re.compile(rf"(?i:\bwe(?:'re|\u2019re| are)|\bhum)\s+(?P<name>{_NAME})")
_SIGNATURE_RE = re.compile(
    r"[-\u2013\u2014]\s*(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)(?:,\s*(?P<org>[^.\n]{2,60}?))?\s*\.?\s*$"
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9&'\u2019]+")
_ADDRESS_RE = re.compile(r"https?://\S+|[\w.+-]+@[\w.-]+", re.I)
_FORWARDED_FROM_RE = re.compile(
    r"(?:forwarded message|original message)[\s\S]{0,80}?\bfrom:\s*(?:[^<\n]*<)?([\w.+-]+@[\w.-]+)", re.I
)


def _strip_possessive(name: str) -> str:
    return re.sub(r"['\u2019]s?$", "", name.strip())


def _mention_matching_label(text: str, label: str) -> str | None:
    """Find how the email itself writes a domain name, e.g. dewlab.in -> "Dew Lab"."""
    target = normalize_key(label)
    if not target:
        return None
    text = _ADDRESS_RE.sub(" ", text)  # "partnerships@dewlab.in" must not shadow "Dew Lab"
    tokens = list(_TOKEN_RE.finditer(text))
    for size in (1, 2, 3):
        for i in range(len(tokens) - size + 1):
            window = tokens[i: i + size]
            candidate = _strip_possessive(text[window[0].start(): window[-1].end()])
            if normalize_key(candidate) == target:
                return candidate
    return None


def _title_from_label(label: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[-_]+", label) if part)


def extract_parties(email: EmailRecord) -> tuple[str | None, str | None, str | None]:
    """Return (brand, agency, contact_name) using generic patterns, never a fixed brand list."""
    text = f"{email.subject}\n{email.body}"
    forwarded = _FORWARDED_FROM_RE.search(email.body)
    domain = sender_domain(forwarded.group(1) if forwarded else email.sender)
    label = "" if is_freemail(domain) else domain_label(domain)
    sender_org = (_mention_matching_label(text, label) or _title_from_label(label)) if label else None

    signature = _SIGNATURE_RE.search(email.body.strip())
    contact = signature.group("name") if signature else None

    represented = _REPRESENTED_RE.search(text)
    if represented:
        brand = _strip_possessive(represented.group("name"))
        if not sender_org or normalize_key(sender_org) != normalize_key(brand):
            agency = (signature.group("org") if signature and signature.group("org") else sender_org)
            return brand, agency, contact
    if sender_org:
        return sender_org, None, contact
    intro = _SELF_INTRO_RE.search(text)
    if intro:
        return _strip_possessive(intro.group("name")), None, contact
    return None, None, contact


# ---------------------------------------------------------------- campaign, deliverables, terms
_PRODUCT_RE = re.compile(
    r"(?i:\b(?:launching|launch of|introducing|featuring|reviewing|promoting|promote|send you)"
    r"\s+(?:a |an |the |our )?(?:brand[- ]new |new |latest )?)"
    r"(?P<p>[\w&'\u2019 -]{2,40}?)"
    r"(?=\s*(?:[.,;:!?()]|$|\b(?:to|for|with|in|on|and|worth|between)\b))"
)
_CAMPAIGN_RE = re.compile(
    r"(?i:\b(?:the|our|this)\s+)(?P<c>[\w&'\u2019 -]{2,40}?)\s+(?i:campaign)\b"
    r"|(?P<owner>[A-Z][\w]*)['\u2019]s\s+(?P<c2>[\w ]{2,30}?)\s+(?P<kw>(?i:offer|campaign|launch|sale))\b"
)
_QTY_WORDS = {"a": 1, "an": 1, "one": 1, "ek": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_DELIVERABLE_RE = re.compile(
    r"\b(?P<qty>\d{1,2}|a|an|one|two|three|four|five|six|ek)\s+"
    r"(?:(?P<len>\d{1,3}(?:\s*(?:-|\u2013|to)\s*\d{1,3})?)[- ]?(?:seconds?|secs?)\s+)?"
    r"(?P<ded>dedicated\s+)?(?:(?P<plat>instagram|insta|ig|youtube|yt)\s+)?"
    r"(?P<type>reels?|stories|story|posts?|shorts?|integrations?|shout-?outs?|videos?)\b",
    re.I,
)
_TYPE_MAP = {
    "reel": "reel", "reels": "reel", "story": "story", "stories": "story", "post": "post",
    "posts": "post", "short": "short", "shorts": "short", "integration": "integration",
    "integrations": "integration", "shoutout": "shoutout", "shoutouts": "shoutout",
    "shout-out": "shoutout", "shout-outs": "shoutout", "video": "video", "videos": "video",
}
_PLATFORM_MAP = {"instagram": "instagram", "insta": "instagram", "ig": "instagram",
                 "youtube": "youtube", "yt": "youtube"}

# Ad-usage phrasings that grant usage rights without saying "usage" or "rights".
_AD_USAGE = (
    r"(?:paid|partnership|spark|whitelist\w*|boost\w*|dark|branded content)\s+ads?\b"
    r"|\bads?\s+(?:from|on|through|via)\s+your\s+(?:handle|account|profile|page)\b"
    r"|\brun (?:it|them|the (?:reel|video|content|post)s?)\s+as\s+(?:\w+\s+)?ads?\b"
)
_USAGE_SENTENCE_RE = re.compile(
    r"\b(usage|rights|whitelist\w*|white-list\w*|licen[cs]\w*|repurpos\w*|in perpetuity)\b|" + _AD_USAGE, re.I
)
_PERPETUAL_RE = re.compile(
    r"perpetu|in perpetuity|\bforever\b|\blifetime\b|unlimited (?:usage|use|duration|time)", re.I
)
_N = r"\d{1,2}|one|two|three|six|twelve"
_UNIT = r"months?|weeks?|years?|days?"
_DURATION_RE = re.compile(
    rf"(?P<n>{_N})\s*(?:-\s*)?(?P<unit>{_UNIT})"
    r"(?:'s)?\s+(?:of\s+)?(?:paid\s+)?(?:ads?\s+|media\s+|digital\s+)?(?:usage|use|rights|licen)"
    rf"|(?:usage|rights|licen[cs]e)\w*\s+(?:for|of)\s+(?P<n2>{_N})\s*(?P<unit2>{_UNIT})"
    # "run it as partnership ads from your handle for 12 months"
    rf"|\b(?:ads?|whitelisting|boost(?:ing)?)\b[^.]{{0,50}}?\bfor\s+(?P<n3>{_N})\s*(?P<unit3>{_UNIT})",
    re.I,
)
_PAID_ADS_RE = re.compile(r"\bpaid (?:ads?|usage|media|social|advertis\w*)\b|\bads?\b|advertis", re.I)
_WHITELIST_RE = re.compile(
    r"whitelist|white-list|allowlist|spark ads?|partnership ads?|branded content ads?|dark posts?"
    r"|(?:run\s+)?ads?\s+(?:from|on|through|via)\s+your\s+(?:handle|account|profile|page)",
    re.I,
)
# "add one more story", "plus 2 extra reels": changes to earlier deliverables, not a full restatement.
_ADDITION_RE = re.compile(
    r"\b(?:add|adding|plus|include|including)\s+(?P<qty>\d{1,2}|a|an|one|two|three|ek)\s+"
    r"(?:more|extra|additional)\s+(?:(?P<plat>instagram|insta|ig|youtube|yt)\s+)?"
    r"(?P<type>reels?|stories|story|posts?|shorts?|integrations?|shout-?outs?|videos?)\b",
    re.I,
)
_ALL_MEDIA_RE = re.compile(
    r"\ball media|\bany media|all channels|all platforms|any platform|throughout the (?:world|universe)",
    re.I,
)
_EXCLUSIVITY_RE = re.compile(
    r"\bexclusiv\w*|\bnot (?:work|partner|collaborate) with (?:any )?(?:other|competing)", re.I
)
_PAYMENT_TERMS_RE = re.compile(
    r"\b(payment|paid within|net\s?\d{1,3}|advance|on delivery|invoice|milestone|instal{1,2}ments?)\b",
    re.I,
)
_COMMISSION_RE = re.compile(r"\d{1,2}(?:\.\d+)?\s*%\s*(?:commission|affiliate|revenue share|of sales)", re.I)
_GIFTING_RE = re.compile(
    r"\bsend you (?:our|a|an|the|some)\b|\bfree (?:product|kit|sample|hamper|box)|\bgift(?:ed|ing)?\b"
    r"|\bbarter\b|\bin (?:return|exchange)\b",
    re.I,
)
_ROLE_PATTERNS: list[tuple[MessageRole, re.Pattern[str]]] = [
    ("revision", re.compile(
        r"\b(revised|revise|updated?|instead|now do|can now|new budget|correction|reduced|increased"
        r"|change[ds]? (?:the|to|our))\b", re.I)),
    ("counter_offer", re.compile(r"\bcounter[- ]?offer|\bcan you do\b|\bwould you accept\b", re.I)),
    ("confirmation", re.compile(r"\b(confirm(?:ed|ing)?|finali[sz]e|agreed|as agreed)\b", re.I)),
    ("follow_up", re.compile(
        r"\bfollow(?:ing)?[- ]?up\b|\bfollowing (?:their|our|my|the) (?:earlier|previous)"
        r"|\bearlier (?:email|message)s?\b|\bjust checking in\b", re.I)),
]
_PRIOR_REFERENCE_RE = re.compile(
    r"\bsame (?:deliverables|terms|scope)\b|\bas (?:discussed|agreed|mentioned)\b"
    r"|\b(?:our|the) (?:earlier |previous )?offer\b|\bearlier (?:email|message)s?\b"
    r"|\bfollow(?:ing)?[- ]?up\b|\bfollowing (?:their|our|my|the) (?:earlier|previous)",
    re.I,
)
_REPLY_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fw|fwd|aw)\s*:\s*)+", re.I)
_ATTACHMENT_RE = re.compile(r"\battach(?:ed|ment)s?\b|\benclosed\b", re.I)
_COLLAB_RE = re.compile(
    r"\b(collab\w*|partner\w*|campaign|sponsor\w*|promot\w*|brand deal|ambassador|integration"
    r"|work with you|influencer|budget|fee|send you|in return|gift(?:ed|ing)?|barter)\b",
    re.I,
)
_NEWSLETTER_SENDERS = {"newsletter", "news", "digest", "updates", "noreply", "no-reply", "donotreply", "mailer"}
_NEWSLETTER_RE = re.compile(
    r"\bunsubscribe\b|view (?:this email )?in (?:your )?browser|\bthis week in\b|\bweekly roundup\b", re.I
)


def strip_reply_prefix(subject: str) -> str:
    return _REPLY_PREFIX_RE.sub("", subject).strip()


def _quantity(token: str) -> int | None:
    token = token.lower()
    return int(token) if token.isdigit() else _QTY_WORDS.get(token)


def extract_deliverables(text: str) -> tuple[list[Deliverable], list[str]]:
    found: list[Deliverable] = []
    notes: list[str] = []
    lowered = text.lower()
    for m in _DELIVERABLE_RE.finditer(text):
        kind = _TYPE_MAP[m.group("type").lower()]
        if m.group("ded") and kind == "video":
            kind = "dedicated_video"
        platform = _PLATFORM_MAP.get((m.group("plat") or "").lower())
        if platform is None:
            sentence = (find_sentence(text, re.escape(m.group())) or "").lower()
            if kind in ("reel", "story", "post"):
                platform = "instagram"
                if "instagram" not in lowered:
                    notes.append(f"Platform for '{m.group().strip()}' not stated; assumed Instagram.")
            elif "youtube" in sentence or (kind in ("short", "dedicated_video") and "youtube" in lowered):
                platform = "youtube"
            else:
                platform = "unknown"
        details = f"{m.group('len')} seconds" if m.group("len") else None
        item = Deliverable(platform=platform, type=kind, quantity=_quantity(m.group("qty")), details=details)
        if item not in found:
            found.append(item)
    return found, notes


def extract_additions(text: str) -> list[Deliverable]:
    """Deliverables added on top of earlier terms ("add one more story")."""
    found = []
    for m in _ADDITION_RE.finditer(text):
        kind = _TYPE_MAP[m.group("type").lower()]
        platform = _PLATFORM_MAP.get((m.group("plat") or "").lower()) or (
            "instagram" if kind in ("reel", "story", "post") else "unknown")
        found.append(Deliverable(platform=platform, type=kind, quantity=_quantity(m.group("qty"))))
    return found


def _sentence_at(text: str, pos: int) -> str:
    return next((text[s:e] for s, e in sentence_spans(text) if s <= pos < e), text)


def _months(n: str, unit: str) -> float:
    qty = float(n) if n.isdigit() else float({"one": 1, "two": 2, "three": 3, "six": 6, "twelve": 12}[n.lower()])
    unit = unit.lower().rstrip("s")
    return round({"month": qty, "week": qty / 4.345, "year": qty * 12, "day": qty / 30}[unit], 2)


def extract_usage_rights(text: str) -> UsageRights | None:
    sentences = [s for s in split_sentences(text) if _USAGE_SENTENCE_RE.search(s)]
    if not sentences:
        return None
    joined = " ".join(sentences)
    duration = None
    m = _DURATION_RE.search(joined)
    if m:
        duration = _months(m.group("n") or m.group("n2") or m.group("n3"),
                           m.group("unit") or m.group("unit2") or m.group("unit3"))
    return UsageRights(
        raw_text=truncate(joined, 400),
        duration_months=duration,
        perpetual=bool(_PERPETUAL_RE.search(joined)),
        paid_ads=bool(_PAID_ADS_RE.search(joined)),
        whitelisting=bool(_WHITELIST_RE.search(joined)),
        all_media=bool(_ALL_MEDIA_RE.search(joined)),
    )


def detect_message_role(email: EmailRecord, has_terms: bool) -> MessageRole:
    text = f"{email.subject}\n{email.body}"
    for role, pattern in _ROLE_PATTERNS:
        if pattern.search(text):
            return role
    if _REPLY_PREFIX_RE.match(email.subject):
        return "follow_up"
    return "initial_offer" if has_terms else "other"


def is_newsletter(email: EmailRecord) -> bool:
    local = sender_address(email.sender).partition("@")[0]
    return local in _NEWSLETTER_SENDERS or bool(_NEWSLETTER_RE.search(email.body))


def extract_with_rules(email: EmailRecord, fallback_reason: str | None = None) -> Extraction:
    """Deterministic extraction. Leaves anything it cannot read as None; never guesses values."""
    body = email.body
    text = f"{email.subject}\n{body}"
    uncertainties: list[str] = []

    brand, agency, contact = extract_parties(email)

    product_match = _PRODUCT_RE.search(body)
    product = product_match.group("p").strip() if product_match else None
    campaign_match = _CAMPAIGN_RE.search(text)
    campaign = None
    if campaign_match and campaign_match.group("c"):
        campaign = campaign_match.group("c").strip()
    elif campaign_match:
        campaign = f"{campaign_match.group('c2').strip()} {campaign_match.group('kw').lower()}"

    deliverables, notes = extract_deliverables(body)
    if not deliverables:
        deliverables, notes = extract_deliverables(email.subject)
    uncertainties.extend(notes)
    additions = extract_additions(body)

    money_text = body if find_money(body) else email.subject
    money = find_money(money_text)
    paid = []
    for mention in (m for m in money if m.kind == "compensation"):
        if _INJECTION_RE.search(_sentence_at(money_text, mention.start)):
            uncertainties.append(f"Amount {mention.raw} appears inside instructions aimed at an AI; not an offer.")
        else:
            paid.append(mention)
    distinct = list(dict.fromkeys((m.amount, m.currency) for m in paid))
    compensation = None
    if distinct:
        chosen = paid[-1]
        if len(distinct) > 1:
            cue = re.compile(r"\b(now|revised|final|updated|instead|increase\w*|reduc\w*)\b", re.I)
            chosen = next((m for m in reversed(paid) if cue.search(m.prefix)), paid[-1])
            uncertainties.append(
                "Several amounts mentioned: " + ", ".join(f"{a:,.0f}" for a, _ in distinct)
                + f"; using {chosen.amount:,.0f}."
            )
        compensation = Money(amount=chosen.amount, currency=chosen.currency)
        if chosen.currency is None:
            uncertainties.append("Fee currency is not stated in the email.")
    product_values = [m for m in money if m.kind == "product_value"]
    product_value = (
        Money(amount=product_values[0].amount, currency=product_values[0].currency)
        if product_values else None
    )

    gifting = bool(product_value) or bool(_GIFTING_RE.search(body))
    payment_type: PaymentType
    if compensation and gifting and product_value:
        payment_type = "cash_and_gifting"
    elif compensation:
        payment_type = "cash"
    elif _COMMISSION_RE.search(body):
        payment_type = "commission"
    elif gifting and deliverables:
        payment_type = "gifting"
    else:
        payment_type = "unknown"

    timeline = parse_timeline(body, email.received_dt.date())
    if timeline and timeline.year_assumed:
        uncertainties.append("Deadline has no year; inferred from the email's received date.")

    exclusivity_sentence = find_sentence(body, _EXCLUSIVITY_RE)
    exclusivity = None
    if exclusivity_sentence:
        m = re.search(r"(\d{1,2}|one|two|three|six|twelve)\s*(months?|weeks?|years?|days?)",
                      exclusivity_sentence, re.I)
        exclusivity = ExclusivityTerm(
            raw_text=exclusivity_sentence, duration_months=_months(m.group(1), m.group(2)) if m else None
        )

    has_terms = bool(deliverables or compensation or product_value)
    newsletter = is_newsletter(email)
    references_prior = bool(_PRIOR_REFERENCE_RE.search(text) or _REPLY_PREFIX_RE.match(email.subject))
    collaboration = has_terms or ((bool(_COLLAB_RE.search(text)) or references_prior) and not newsletter)

    extraction = Extraction(
        source="rules",
        fallback_reason=fallback_reason,
        is_brand_collaboration=collaboration,
        brand=brand,
        agency=agency,
        contact_name=contact,
        contact_email=sender_address(email.sender),
        campaign_name=campaign,
        product=product,
        deliverables=deliverables,
        deliverable_additions=additions,
        compensation=compensation,
        payment_type=payment_type,
        product_value=product_value,
        timeline=timeline,
        usage_rights=extract_usage_rights(body),
        exclusivity=exclusivity,
        payment_terms=find_sentence(body, _PAYMENT_TERMS_RE),
        message_role=detect_message_role(email, has_terms),
        references_prior_message=references_prior,
        mentions_attachment=bool(_ATTACHMENT_RE.search(body)),
        uncertainties=uncertainties,
    )
    extraction.missing_information = missing_from_extraction(extraction)
    return extraction


def missing_from_extraction(extraction: Extraction) -> list[str]:
    if not extraction.is_brand_collaboration:
        return []
    missing = []
    if not extraction.deliverables:
        missing.append("deliverables")
    if extraction.compensation is None and extraction.payment_type in ("unknown", "commission"):
        missing.append("compensation")
    if extraction.timeline is None:
        missing.append("timeline")
    if extraction.usage_rights is None:
        missing.append("usage_rights")
    if extraction.payment_terms is None and extraction.compensation is not None:
        missing.append("payment_terms")
    return missing


# ---------------------------------------------------------------- security signals
_OFFICIAL_DOMAINS = {
    "youtube": ("youtube.com", "google.com"),
    "google": ("google.com",),
    "instagram": ("instagram.com", "facebookmail.com", "meta.com"),
    "facebook": ("facebook.com", "facebookmail.com", "meta.com"),
    "meta": ("meta.com", "facebookmail.com"),
    "whatsapp": ("whatsapp.com",),
    "paypal": ("paypal.com",),
    "tiktok": ("tiktok.com",),
}
_SUSPICIOUS_TLDS = (".biz", ".xyz", ".top", ".click", ".loan", ".win", ".work", ".gq", ".tk", ".ml")
_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.I)
_UPFRONT_RE = re.compile(
    r"\b(registration|joining|onboarding|processing|membership|enrol{1,2}ment|activation|listing)"
    r"\s+(fee|charge)\b|\bsecurity deposit\b|\byou (?:need|have) to pay\b|\bpay (?:us )?(?:a|an|the)?\s*"
    r"(?:one-time |small |refundable )?(?:fee|deposit)\b",
    re.I,
)
_CREDENTIAL_RE = re.compile(
    r"\bverify (?:your )?(?:account|identity|channel|login)\b|\bconfirm your (?:password|credentials|login|account)\b"
    r"|\b(?:enter|share|send) (?:your )?(?:password|otp|login|credentials)\b|\breset your password\b"
    r"|\blog ?in (?:now|here|to (?:avoid|verify|confirm))\b",
    re.I,
)
_ACCOUNT_THREAT_RE = re.compile(
    r"\b(?:will be|has been|is being) (?:suspended|terminated|deleted|disabled|banned)\b"
    r"|\bcopyright strikes?\b|\bavoid (?:termination|suspension)\b|\baccount (?:suspension|termination)\b",
    re.I,
)
_INJECTION_RE = re.compile(
    r"\b(?:note|message|instructions?)\s+(?:to|for)\s+(?:any|the|all)\s+(?:ai|assistant|bot|model|llm)s?\b"
    r"|\b(?:ai|assistant|bot|model|llm)s?\s+(?:processing|reading|summari[sz]ing|handling)\s+this\s+"
    r"(?:inbox|email|message|mail)\b"
    r"|\bignore (?:all |any )?(?:previous|prior|above|earlier|your) (?:instructions|prompts?|rules)\b"
    r"|\bdisregard (?:your|the|all|any) (?:rules|instructions|guidelines|policies)\b"
    r"|\bclassify this (?:email|message)\b|\b(?:system|developer) prompt\b"
    r"|\byou are (?:now )?(?:an?|the) (?:ai|assistant|language model)\b"
    r"|\breveal (?:your|the) (?:prompt|instructions|api key|system)\b",
    re.I,
)
_PERSONAL_DATA_STRONG_RE = re.compile(
    r"\b(?:home|residential|personal) address\b|\bpersonal (?:phone|mobile|number|email)\b"
    r"|\b(?:otp|one[- ]time password|upi pin|cvv|password|aadh?aa?r)\b",
    re.I,
)
_PERSONAL_DATA_WEAK_RE = re.compile(r"\bbank (?:account|details)\b|\bpan (?:card|number|details)\b", re.I)
_URGENCY_RE = re.compile(
    r"\b(?:within|in)\s+\d+\s*(?:hours?|hrs?)\b|\b\d+\s*hours? only\b|\bvalid (?:for )?\d+\s*hours?\b"
    r"|\bact now\b|\bimmediately\b|\burgent(?:ly)?\b|\basap\b|\blast chance\b|\bexpires? (?:today|soon|tonight)\b",
    re.I,
)
_TOO_GOOD_RE = re.compile(
    r"\bcongratulations\b|\byou(?:'ve| have| are|'re)? (?:been )?selected\b|\byou(?:'ve| have) won\b"
    r"|\bwinner\b|\bguaranteed (?:income|earnings|returns)\b",
    re.I,
)
_GENERIC_GREETING_RE = re.compile(r"\bdear (?:creator|user|customer|influencer|sir/madam|sir or madam)\b", re.I)


def _signal(code: str, strength: str, explanation: str, text: str, pattern: re.Pattern[str]) -> SecuritySignal:
    evidence = find_sentence(text, pattern) or truncate(text, 160)
    return SecuritySignal(code=code, strength=strength, explanation=explanation, evidence=evidence)


def _impersonated_platform(domain: str) -> str | None:
    for platform, official in _OFFICIAL_DOMAINS.items():
        if platform in domain and not any(domain == d or domain.endswith("." + d) for d in official):
            return platform
    return None


def detect_security_signals(email: EmailRecord) -> list[SecuritySignal]:
    """Plain-code safety scan of the raw email. Never depends on (or can be overridden by) the model."""
    text = f"{email.subject}\n{email.body}"
    domain = sender_domain(email.sender)
    has_url = bool(_URL_RE.search(text))
    signals: list[SecuritySignal] = []

    upfront = [m for m in find_money(email.body) if m.kind == "upfront_fee"]
    if upfront or _UPFRONT_RE.search(text):
        signals.append(_signal("upfront_payment_request", "strong",
                               "Asks the creator to pay money to take part.", text, _UPFRONT_RE))
    platform = _impersonated_platform(domain)
    if platform:
        signals.append(SecuritySignal(
            code="platform_impersonation", strength="strong",
            explanation=f"Sender domain '{domain}' uses the '{platform}' name but is not an official {platform} domain.",
            evidence=f"From: {sender_address(email.sender)}"))
    if _CREDENTIAL_RE.search(text):
        signals.append(_signal("credential_phishing", "strong" if has_url else "weak",
                               "Asks the creator to verify or log in to an account.", text, _CREDENTIAL_RE))
    if _ACCOUNT_THREAT_RE.search(text):
        signals.append(_signal("account_threat", "strong" if (has_url or platform) else "weak",
                               "Threatens account suspension or strikes to create pressure.", text, _ACCOUNT_THREAT_RE))
    if _INJECTION_RE.search(text):
        signals.append(_signal("prompt_injection", "strong",
                               "Contains instructions aimed at an AI assistant; treated as data and ignored.",
                               text, _INJECTION_RE))
    if _PERSONAL_DATA_STRONG_RE.search(text):
        signals.append(_signal("personal_data_request", "strong",
                               "Requests private personal data (address, phone, passwords or OTPs).",
                               text, _PERSONAL_DATA_STRONG_RE))
    elif _PERSONAL_DATA_WEAK_RE.search(text):
        signals.append(_signal("financial_data_request", "weak",
                               "Requests bank or PAN details; share only after a contract is verified.",
                               text, _PERSONAL_DATA_WEAK_RE))
    if _URGENCY_RE.search(text):
        signals.append(_signal("urgency_pressure", "weak", "Uses urgency or a short expiry to pressure a decision.",
                               text, _URGENCY_RE))
    if has_url:
        insecure = bool(re.search(r"http://", text, re.I))
        signals.append(_signal("link_present", "weak",
                               "Contains a link" + (" over insecure http" if insecure else "")
                               + "; open the official site directly instead.", text, _URL_RE))
    if domain.endswith(_SUSPICIOUS_TLDS):
        signals.append(SecuritySignal(code="suspicious_domain", strength="weak",
                                      explanation=f"Sender uses a low-reputation top-level domain ({domain}).",
                                      evidence=f"From: {sender_address(email.sender)}"))
    if _TOO_GOOD_RE.search(text):
        signals.append(_signal("too_good_to_be_true", "weak", "Unsolicited 'you are selected' style language.",
                               text, _TOO_GOOD_RE))
    if _GENERIC_GREETING_RE.search(text):
        signals.append(_signal("generic_greeting", "weak", "Generic greeting instead of the creator's name.",
                               text, _GENERIC_GREETING_RE))
    for signal in signals:
        signal.email_id = email.id
    return signals


def is_security_threat(signals: list[SecuritySignal]) -> bool:
    strong = sum(s.strength == "strong" for s in signals)
    weak = sum(s.strength == "weak" for s in signals)
    return strong > 0 or weak >= SCAM_WEAK_SIGNAL_THRESHOLD


# ---------------------------------------------------------------- final category
def decide_category(
    signals: list[SecuritySignal], is_collaboration: bool, risks: list[Risk], missing: list[str]
) -> Category:
    """Ordered, deterministic decision. The model's suggested_category is never consulted here."""
    if is_security_threat(signals):
        return "scam_or_phishing"
    if not is_collaboration:
        return "spam"
    if any(r.blocking for r in risks):
        return "not_a_fit"
    if any(term in missing for term in CRITICAL_TERMS):
        return "needs_more_info"
    return "deal_offer"


def classify(email: EmailRecord, profile: dict):
    """Baseline-compatible single-email entry point; runs the full pipeline for one email."""
    from app.pipeline import analyze_emails

    return analyze_emails([email], profile).analyses[0]
