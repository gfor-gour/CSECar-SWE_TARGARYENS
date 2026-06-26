from fastapi import APIRouter
from app.models.request import TicketRequest
from app.models.response import TicketResponse
from app.services.analyzer import analyze_ticket

router = APIRouter()


@router.post("/analyze-ticket", response_model=TicketResponse)
def analyze(ticket: TicketRequest):
    """
    Synchronous handler: analyze_ticket is a pure function (no I/O, no
    awaiting). FastAPI runs sync handlers in a threadpool, so this won't
    block the event loop.
    """
    return analyze_ticket(ticket)
