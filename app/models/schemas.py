# Defines validated input email and structured analysis output schemas.
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
Category = Literal["deal_offer", "needs_more_info", "not_a_fit", "scam_or_phishing", "spam"]
class EmailRecord(BaseModel):
    id: str
    received_at: str
    sender: str = Field(validation_alias="from")
    subject: str
    body: str
    model_config = ConfigDict(populate_by_name=True)
    @property
    def from_(self): return self.sender
class Analysis(BaseModel):
    email_id: str
    category: Category
    brand: str | None = None
    contact: str | None = None
    platform: str | None = None
    deliverables: list[str] = Field(default_factory=list)
    fee: float | None = None
    currency: str | None = None
    payment_type: Literal["cash", "gifting", "unknown"] = "unknown"
    deadline: str | None = None
    usage_rights: str | None = None
    risk_flags: list[str] = Field(default_factory=list)
    recommendation: str
    draft_reply: str | None = None
    deal_key: str | None = None
