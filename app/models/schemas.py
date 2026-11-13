# Pydantic models for inbox input, per-email extraction, reconciled deal terms, risks and recommendations.
# Email-level records (Analysis) and deal-level records (Deal) are deliberately kept separate.
from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import LOCAL_TZ

Category = Literal["deal_offer", "needs_more_info", "not_a_fit", "scam_or_phishing", "spam"]
CATEGORIES: tuple[str, ...] = get_args(Category)
Action = Literal[
    "accept",
    "negotiate",
    "request_more_information",
    "decline",
    "ignore",
    "flag_for_security_review",
    "review_manually",
]
Severity = Literal["low", "medium", "high", "critical"]
PaymentType = Literal["cash", "gifting", "cash_and_gifting", "commission", "unknown"]
MessageRole = Literal[
    "initial_offer", "follow_up", "revision", "confirmation", "counter_offer", "other"
]
ExtractionSource = Literal["groq", "gemini", "rules"]
Platform = Literal["instagram", "youtube", "other", "unknown"]
DeliverableType = Literal[
    "reel", "story", "post", "short", "integration", "dedicated_video", "shoutout", "video", "other"
]
CheckStatus = Literal["pass", "fail", "warning", "unknown", "not_applicable"]
RiskCategory = Literal[
    "security",
    "content_policy",
    "exclusivity",
    "rate",
    "compensation",
    "usage_rights",
    "deadline",
    "missing_information",
    "verification",
    "terms",
    "platform",
    "currency",
]

T = TypeVar("T")


# ---------------------------------------------------------------- input
class EmailRecord(BaseModel):
    id: str
    received_at: str
    sender: str = Field(validation_alias="from")
    subject: str
    body: str
    # Optional mail headers; the sample dataset has none, real inboxes usually do.
    message_id: str | None = None
    thread_id: str | None = None
    in_reply_to: str | None = None
    references: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("received_at")
    @classmethod
    def _parseable_timestamp(cls, value: str) -> str:
        datetime.fromisoformat(value.strip())  # raises ValueError on malformed input
        return value

    @property
    def from_(self) -> str:
        return self.sender

    @property
    def received_dt(self) -> datetime:
        parsed = datetime.fromisoformat(self.received_at.strip())
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=LOCAL_TZ)


# ---------------------------------------------------------------- extracted terms
class Deliverable(BaseModel):
    platform: Platform = "unknown"
    type: DeliverableType
    quantity: int | None = Field(default=None, ge=1, le=100)
    details: str | None = Field(default=None, max_length=200)

    @property
    def rate_key(self) -> str:
        return f"{self.platform}_{self.type}"

    def label(self) -> str:
        qty = f"{self.quantity} x " if self.quantity else "? x "
        names = {"instagram": "Instagram ", "youtube": "YouTube "}
        platform = names.get(self.platform, "")
        return f"{qty}{platform}{self.type.replace('_', ' ')}"


class Money(BaseModel):
    amount: float = Field(ge=0)
    currency: str | None = None  # ISO code; None when the email does not state one

    @field_validator("currency")
    @classmethod
    def _iso_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = value.strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError(f"currency must be a 3-letter code, got {value!r}")
        return code


class Timeline(BaseModel):
    raw_text: str = Field(max_length=300)
    start_date: date | None = None
    end_date: date | None = None
    relative: str | None = None  # e.g. "within 2 weeks of delivery"
    year_assumed: bool = False


class UsageRights(BaseModel):
    raw_text: str = Field(max_length=400)
    duration_months: float | None = Field(default=None, ge=0)
    perpetual: bool = False
    paid_ads: bool = False
    whitelisting: bool = False
    all_media: bool = False


class ExclusivityTerm(BaseModel):
    raw_text: str = Field(max_length=400)
    duration_months: float | None = Field(default=None, ge=0)


class SecuritySignal(BaseModel):
    code: str
    strength: Literal["strong", "weak"]
    explanation: str
    evidence: str
    email_id: str | None = None


class Extraction(BaseModel):
    """Facts read from one email. Contains no policy judgement."""

    source: ExtractionSource
    model: str | None = None
    fallback_reason: str | None = None
    is_brand_collaboration: bool = False
    suggested_category: Category | None = None  # model opinion only; never final
    brand: str | None = None
    agency: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    campaign_name: str | None = None
    product: str | None = None
    product_category: str | None = None
    deliverables: list[Deliverable] = Field(default_factory=list)
    # Items added on top of earlier terms ("add one more story"); applied during reconciliation.
    deliverable_additions: list[Deliverable] = Field(default_factory=list)
    compensation: Money | None = None
    payment_type: PaymentType = "unknown"
    product_value: Money | None = None
    timeline: Timeline | None = None
    usage_rights: UsageRights | None = None
    exclusivity: ExclusivityTerm | None = None
    payment_terms: str | None = None
    message_role: MessageRole = "other"
    references_prior_message: bool = False
    mentions_attachment: bool = False
    missing_information: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    security_concerns: list[str] = Field(default_factory=list)
    prompt_injection_detected: bool = False


# ---------------------------------------------------------------- reconciliation
class TermEvent(BaseModel, Generic[T]):
    email_id: str
    received_at: str
    value: T
    change_type: Literal["stated", "confirmed", "revised", "changed_without_cue"]


class TermValue(BaseModel, Generic[T]):
    value: T
    source_email_id: str
    source_received_at: str
    status: Literal["stated", "confirmed", "revised", "changed_without_cue"]
    history: list[TermEvent[T]] = Field(default_factory=list)


class ReconciledTerms(BaseModel):
    as_of_email_id: str | None = None
    brand: str | None = None
    agency: str | None = None
    campaign: str | None = None
    compensation: TermValue[Money] | None = None
    payment_type: TermValue[str] | None = None
    product_value: TermValue[Money] | None = None
    deliverables: TermValue[list[Deliverable]] | None = None
    platforms: TermValue[list[str]] | None = None
    timeline: TermValue[Timeline] | None = None
    usage_rights: TermValue[UsageRights] | None = None
    exclusivity: TermValue[ExclusivityTerm] | None = None
    payment_terms: TermValue[str] | None = None
    ambiguities: list[str] = Field(default_factory=list)

    def current(self, name: str) -> Any:
        term = getattr(self, name)
        return term.value if term is not None else None


# ---------------------------------------------------------------- assessment
class Evidence(BaseModel):
    source: Literal["email", "creator_profile", "computed"]
    email_id: str | None = None
    quote: str


class Risk(BaseModel):
    code: str
    category: RiskCategory
    severity: Severity
    blocking: bool = False  # True means the creator's rules make this deal not a fit
    title: str
    explanation: str
    evidence: list[Evidence] = Field(default_factory=list)
    term: str | None = None
    mitigation: str


class PolicyCheck(BaseModel):
    name: str
    status: CheckStatus
    detail: str


class RateLine(BaseModel):
    deliverable: str
    quantity: int
    unit_rate_inr: float
    subtotal_inr: float


class RateCheck(BaseModel):
    status: Literal["meets_rate", "below_rate", "unpriced_items", "unavailable", "not_applicable"]
    offered_amount: float | None = None
    offered_currency: str | None = None
    fx_rate_to_inr: float | None = None
    offered_inr: float | None = None
    minimum_inr: float | None = None
    shortfall_inr: float | None = None
    shortfall_pct: float | None = None
    lines: list[RateLine] = Field(default_factory=list)
    unpriced: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    detail: str


class Recommendation(BaseModel):
    action: Action
    summary: str
    reasons: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class DraftReply(BaseModel):
    """A reply for the creator to review and send themselves. Never sent automatically."""

    to: str
    subject: str
    body: str
    notes: list[str] = Field(default_factory=list)  # what the creator should check before sending


class Assessment(BaseModel):
    category: Category
    rate_check: RateCheck | None = None
    policy_checks: list[PolicyCheck] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    recommendation: Recommendation


# ---------------------------------------------------------------- outputs
class GroupingEvidence(BaseModel):
    email_id: str
    linked_email_id: str
    reason: str
    strength: Literal["strong", "medium", "duplicate"]


class RelatedLink(BaseModel):
    email_id: str
    other_email_id: str
    reason: str


class Analysis(BaseModel):
    """Email-level record: this email's own extraction plus its assessment in deal context."""

    email_id: str
    received_at: str = ""
    sender: str = ""
    subject: str = ""
    category: Category
    deal_id: str | None = None
    deal_key: str | None = None  # baseline-compatible alias of deal_id
    is_latest_in_deal: bool = True
    extraction: Extraction | None = None
    security_signals: list[SecuritySignal] = Field(default_factory=list)
    terms_in_context: ReconciledTerms | None = None
    rate_check: RateCheck | None = None
    policy_checks: list[PolicyCheck] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    recommendation_detail: Recommendation | None = None
    # Flattened fields kept from the baseline schema (this email's own extracted values).
    brand: str | None = None
    contact: str | None = None
    platform: str | None = None
    deliverables: list[str] = Field(default_factory=list)
    fee: float | None = None
    currency: str | None = None
    payment_type: PaymentType = "unknown"
    deadline: str | None = None
    usage_rights: str | None = None
    risk_flags: list[str] = Field(default_factory=list)
    recommendation: str = ""
    draft_reply: str | None = None


class Deal(BaseModel):
    """Deal-level record: grouped emails with reconciled latest terms and a final recommendation."""

    deal_id: str
    deal_key: str
    brand: str | None = None
    agency: str | None = None
    campaign: str | None = None
    email_ids: list[str]
    latest_email_id: str
    first_received_at: str
    last_received_at: str
    extraction_sources: dict[str, str] = Field(default_factory=dict)
    grouping_evidence: list[GroupingEvidence] = Field(default_factory=list)
    possible_related: list[RelatedLink] = Field(default_factory=list)
    category: Category
    terms: ReconciledTerms
    security_signals: list[SecuritySignal] = Field(default_factory=list)
    rate_check: RateCheck | None = None
    policy_checks: list[PolicyCheck] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    recommendation: Recommendation
    draft_reply: DraftReply | None = None
