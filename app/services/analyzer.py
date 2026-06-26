from app.models.request import TicketRequest
from app.models.response import TicketResponse

async def analyze_ticket(ticket: TicketRequest) -> TicketResponse:
    """
    Main analysis pipeline.
    Dev 2 owns: transaction matching, evidence verdict, classification, routing.
    Dev 3 owns: LLM call, prompt, safety guardrails, customer_reply generation.
    This stub returns a valid dummy response so the API works end-to-end immediately.
    """
    # STUB — replace with real logic
    return TicketResponse(
        ticket_id=ticket.ticket_id,
        relevant_transaction_id=None,
        evidence_verdict="insufficient_data",
        case_type="other",
        severity="low",
        department="customer_support",
        agent_summary="Stub response — analysis not yet implemented.",
        recommended_next_action="Review manually.",
        customer_reply="Thank you for reaching out. Our team will review your case. Please do not share your PIN or OTP with anyone.",
        human_review_required=True,
        confidence=0.0,
        reason_codes=["stub"]
    )
