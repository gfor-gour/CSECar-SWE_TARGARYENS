import asyncio
from fastapi import APIRouter, HTTPException
from app.models.request import TicketRequest
from app.models.response import TicketResponse
from app.services.analyzer import analyze_ticket

router = APIRouter()

@router.post("/analyze-ticket", response_model=TicketResponse)
async def analyze(ticket: TicketRequest):
    try:
        result = await asyncio.wait_for(analyze_ticket(ticket), timeout=28.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=500, detail="Analysis timed out")
    return result
