# Pure parsing helpers shared by rule extraction, AI grounding checks, grouping and policy checks.
# Money (Indian lakh/k formats), dates with year inference, sender domains and evidence sentences.
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from app.models.schemas import Money, Timeline

FREEMAIL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "outlook.com", "hotmail.com",
        "live.com", "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com",
        "rediffmail.com", "zoho.com", "gmx.com",
    }
)
_SECOND_LEVEL = frozenset({"co", "com", "net", "org", "gov", "ac", "edu"})


# ---------------------------------------------------------------- text and identity
def normalize_key(text: str | None) -> str:
    """Lowercase alphanumerics only, possessives removed: "SnapMeal's" -> "snapmeal"."""
    if not text:
        return ""
    text = re.sub(r"['’]s\b", "", text)
    return re.sub(r"[^a-z0-9]", "", text.lower())


def slugify(text: str | None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "unknown"


def sender_address(sender: str) -> str:
    match = re.search(r"<([^>]+)>", sender)
    return (match.group(1) if match else sender).strip().lower()


def sender_domain(sender: str) -> str:
    return sender_address(sender).rpartition("@")[2]


def domain_label(domain: str) -> str:
    """Registrable name of a domain: dewlab.in -> dewlab, brand.co.in -> brand."""
    parts = [p for p in domain.lower().split(".") if p]
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL:
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else ""


def is_freemail(domain: str) -> bool:
    return domain.lower() in FREEMAIL_DOMAINS


def brand_keys_match(a: str | None, b: str | None) -> bool:
    """Same brand if normalized keys are equal or one is a long-enough prefix of the other."""
    ka, kb = normalize_key(a), normalize_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    short, long_ = sorted((ka, kb), key=len)
    return len(short) >= 5 and long_.startswith(short)


_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:(?<=\bRs)\.[^.!?\n]*)*[.!?]?", re.I)


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) offsets of sentences; tolerates "Rs." abbreviations."""
    spans = []
    for match in _SENTENCE_RE.finditer(text):
        if match.group().strip():
            spans.append((match.start(), match.end()))
    return spans


def split_sentences(text: str) -> list[str]:
    return [text[s:e].strip() for s, e in sentence_spans(text)]


def truncate(text: str, limit: int = 240) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def find_sentence(text: str, pattern: str | re.Pattern[str]) -> str | None:
    regex = re.compile(pattern, re.I) if isinstance(pattern, str) else pattern
    for sentence in split_sentences(text):
        if regex.search(sentence):
            return truncate(sentence)
    return None


# ---------------------------------------------------------------- money
_CURRENCY_TOKENS = {
    "inr": "INR", "rs": "INR", "rs.": "INR", "₹": "INR", "rupee": "INR", "rupees": "INR",
    "usd": "USD", "us$": "USD", "$": "USD", "dollar": "USD", "dollars": "USD",
    "eur": "EUR", "€": "EUR", "euro": "EUR", "euros": "EUR",
    "gbp": "GBP", "£": "GBP",
}
_MULTIPLIERS = {
    "k": 1_000, "thousand": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
    "lacs": 100_000, "crore": 10_000_000, "crores": 10_000_000,
}
_NUM = r"\d[\d,]*(?:\.\d+)?"
_MULT = r"(?:lakhs?|lacs?|crores?|thousand|k)\b"
_PRE_RE = re.compile(
    rf"(?<![A-Za-z])(?P<cur>INR|Rs\.?|USD|US\$|EUR|GBP|[$₹€£])\s*(?P<num>{_NUM})"
    rf"(?:\s*(?P<mult>{_MULT}))?",
    re.I,
)
_POST_RE = re.compile(
    rf"(?<![\w.,])(?P<num>{_NUM})(?:\s*(?P<mult>{_MULT}))?\s*"
    rf"(?P<cur>INR|USD|EUR|GBP|rupees?|dollars?|euros?)\b",
    re.I,
)
_BARE_RE = re.compile(rf"(?<![\w.,$₹€£])(?P<num>{_NUM})\s*(?P<mult>{_MULT})", re.I)
_MONEY_CONTEXT_RE = re.compile(
    r"\b(budget|fee|pay|paid|payment|offer|compensation|rate|price|amount|cost|worth|rupees?)\b", re.I
)
_PRODUCT_VALUE_PREFIX = re.compile(
    r"\b(worth|valued at|value of|mrp|retail (?:value|price))\b[^.]{0,12}$", re.I
)
_UPFRONT_PREFIX = re.compile(
    r"\b(registration|joining|onboarding|processing|membership|enrol{1,2}ment|activation|listing)"
    r"\s+(fee|charge|amount)\b|\bsecurity deposit\b|\bdeposit of\b"
    r"|\byou (?:need|have|will need) to pay\b|\byou must pay\b|\bpay us\b",
    re.I,
)
_IMPERATIVE_PAY = re.compile(r"^\s*(?:please\s+|kindly\s+|just\s+)?pay\b", re.I)

MoneyKind = Literal["compensation", "product_value", "upfront_fee"]


@dataclass(frozen=True)
class MoneyMention:
    amount: float
    currency: str | None
    start: int
    end: int
    raw: str
    kind: MoneyKind
    prefix: str  # sentence text before the amount, used for revision cues


def _to_amount(num: str, mult: str | None) -> float | None:
    try:
        value = float(num.replace(",", ""))
    except ValueError:
        return None
    if mult:
        value *= _MULTIPLIERS.get(mult.lower(), 1)
    return value


def find_money(text: str) -> list[MoneyMention]:
    """All monetary amounts in text, classified as compensation, product value or upfront fee."""
    spans = sentence_spans(text)
    found: list[MoneyMention] = []
    taken: list[tuple[int, int]] = []

    def sentence_prefix(pos: int) -> tuple[str, str]:
        for s, e in spans:
            if s <= pos < e:
                return text[s:pos], text[s:e]
        return text[:pos], text

    for regex, needs_context in ((_PRE_RE, False), (_POST_RE, False), (_BARE_RE, True)):
        for m in regex.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            prefix, sentence = sentence_prefix(m.start())
            if needs_context and not _MONEY_CONTEXT_RE.search(sentence):
                continue  # "85k followers" is not money
            amount = _to_amount(m.group("num"), m.groupdict().get("mult"))
            if amount is None:
                continue
            cur_token = (m.groupdict().get("cur") or "").lower()
            currency = _CURRENCY_TOKENS.get(cur_token) if cur_token else None
            if _PRODUCT_VALUE_PREFIX.search(prefix):
                kind: MoneyKind = "product_value"
            elif _UPFRONT_PREFIX.search(prefix) or _IMPERATIVE_PAY.search(prefix):
                kind = "upfront_fee"
            else:
                kind = "compensation"
            found.append(MoneyMention(amount, currency, m.start(), m.end(), m.group(), kind, prefix))
            taken.append((m.start(), m.end()))
    return sorted(found, key=lambda x: x.start)


def amount_in_text(amount: float, text: str) -> bool:
    """Grounding check: is this amount literally present (in any supported format)?"""
    return any(abs(m.amount - amount) < 0.5 for m in find_money(text))


def currency_in_text(currency: str, text: str) -> bool:
    return any(m.currency == currency for m in find_money(text))


def format_amount(amount: float, currency: str | None = None) -> str:
    """Indian digit grouping for INR (1,20,000); western grouping otherwise."""
    whole = int(round(amount))
    if currency in (None, "INR"):
        digits = str(abs(whole))
        if len(digits) > 3:
            head, tail = digits[:-3], digits[-3:]
            groups = []
            while len(head) > 2:
                groups.insert(0, head[-2:])
                head = head[:-2]
            if head:
                groups.insert(0, head)
            digits = ",".join(groups + [tail])
        text = ("-" if whole < 0 else "") + digits
    else:
        text = f"{whole:,}"
    return f"{currency} {text}" if currency else text


def format_money(money: Money | None) -> str:
    if money is None:
        return "not stated"
    if money.currency is None:
        return f"{format_amount(money.amount)} (currency not stated)"
    return format_amount(money.amount, money.currency)


# ---------------------------------------------------------------- dates
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MON = (
    r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_ORD = r"(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s+(?P<year>20\d{2}))?"
_RANGE_RE = re.compile(
    rf"(?:between\s+)?\b(?P<d1>\d{{1,2}}){_ORD}\s*(?:-|–|to|and|until|till)\s*"
    rf"(?P<d2>\d{{1,2}}){_ORD}\s+{_MON}\b{_YEAR}",
    re.I,
)
_DAY_MON_RE = re.compile(rf"\b(?P<day>\d{{1,2}}){_ORD}\s+(?:of\s+)?{_MON}\b{_YEAR}", re.I)
_MON_DAY_RE = re.compile(rf"\b{_MON}\s+(?P<day>\d{{1,2}}){_ORD}\b{_YEAR}", re.I)
_ISO_RE = re.compile(r"\b(?P<year>20\d{2})-(?P<m>\d{2})-(?P<day>\d{2})\b")
_RELATIVE_RE = re.compile(
    r"\bwithin\s+(?:\d+|one|two|three|four|a)\s+(?:days?|weeks?|months?)(?:\s+of\s+[\w ]{3,30})?",
    re.I,
)
_START_CUE = re.compile(r"\b(from|starting|start(?:s|ing)? on|after|begin(?:ning)?)\s*$", re.I)
YEAR_ROLLOVER_DAYS = 90


def infer_date(day: int, month: int, year: int | None, anchor: date) -> tuple[date | None, bool]:
    """Build a date; when the year is missing, use the anchor's year unless that is >90 days past."""
    try:
        if year is not None:
            return date(year, month, day), False
        candidate = date(anchor.year, month, day)
        if candidate < anchor - timedelta(days=YEAR_ROLLOVER_DAYS):
            candidate = date(anchor.year + 1, month, day)
        return candidate, True
    except ValueError:
        return None, False


def parse_timeline(text: str, anchor: date) -> Timeline | None:
    """Deterministic deadline/posting-window parser. `anchor` is the email's received date."""
    range_match = _RANGE_RE.search(text)
    if range_match:
        month = _MONTHS[range_match.group("mon")[:3].lower()]
        year = int(range_match.group("year")) if range_match.group("year") else None
        start, assumed1 = infer_date(int(range_match.group("d1")), month, year, anchor)
        end, assumed2 = infer_date(int(range_match.group("d2")), month, year, anchor)
        if start and end:
            sentence = find_sentence(text, re.escape(range_match.group())) or range_match.group()
            return Timeline(
                raw_text=sentence, start_date=start, end_date=end, year_assumed=assumed1 or assumed2
            )

    singles: list[tuple[date, bool, bool, str]] = []  # (date, assumed, is_start, matched text)
    for regex in (_DAY_MON_RE, _MON_DAY_RE):
        for m in regex.finditer(text):
            month = _MONTHS[m.group("mon")[:3].lower()]
            year = int(m.group("year")) if m.group("year") else None
            parsed, assumed = infer_date(int(m.group("day")), month, year, anchor)
            if parsed:
                is_start = bool(_START_CUE.search(text[max(0, m.start() - 25): m.start()]))
                singles.append((parsed, assumed, is_start, m.group()))
    for m in _ISO_RE.finditer(text):
        parsed, _ = infer_date(int(m.group("day")), int(m.group("m")), int(m.group("year")), anchor)
        if parsed:
            singles.append((parsed, False, False, m.group()))

    relative = _RELATIVE_RE.search(text)
    if not singles and not relative:
        return None
    if not singles:
        assert relative is not None
        sentence = find_sentence(text, re.escape(relative.group())) or relative.group()
        return Timeline(raw_text=sentence, relative=relative.group())

    starts = [s for s in singles if s[2]]
    ends = [s for s in singles if not s[2]]
    start = min(starts, key=lambda s: s[0]) if starts else None
    end = min(ends, key=lambda s: s[0]) if ends else None
    used = [x for x in (start, end) if x]
    sentence = find_sentence(text, re.escape(used[0][3])) or used[0][3]
    return Timeline(
        raw_text=sentence,
        start_date=start[0] if start else None,
        end_date=end[0] if end else None,
        relative=relative.group() if relative else None,
        year_assumed=any(x[1] for x in used),
    )
