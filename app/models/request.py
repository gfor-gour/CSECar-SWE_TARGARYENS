from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator

class TransactionEntry(BaseModel):
    transaction_id: str
    timestamp: str  # ISO 8601
    type: Literal["transfer", "payment", "cash_in", "cash_out", "settlement", "refund"]
    amount: float
    counterparty: str
    status: Literal["completed", "failed", "pending", "reversed"]

class TicketRequest(BaseModel):
    ticket_id: str
    complaint: str
    language: Optional[Literal["en", "bn", "mixed"]] = None
    channel: Optional[Literal["in_app_chat", "call_center", "email", "merchant_portal", "field_agent"]] = None
    user_type: Optional[Literal["customer", "merchant", "agent", "unknown"]] = None
    campaign_context: Optional[str] = None
    transaction_history: Optional[List[TransactionEntry]] = Field(default_factory=list)
    metadata: Optional[dict] = None

    @field_validator("complaint")
    @classmethod
    def validate_complaint(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("complaint field cannot be empty")
        return v
