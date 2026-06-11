"""
Chat endpoint — POST /api/chat.

Frontend sends the full conversation history (client-side state); this endpoint
extracts the latest user message, runs the QuerySmith agent loop, and returns
the new assistant text plus token / cost telemetry.

Singletons are lazily initialized — first request bootstraps the BQClient
(which loads the read-only SA key) and Anthropic client; subsequent requests
reuse them.
"""

import os
from datetime import datetime, timezone

from anthropic import Anthropic
from fastapi import APIRouter, HTTPException

from api.agent import BQClient, run_agent
from api.schemas import ChatRequest, ChatResponse, Message

router = APIRouter(prefix="/api", tags=["chat"])

_bq: BQClient | None = None
_anthropic: Anthropic | None = None

# ---------------------------------------------------------------------------
# Daily spend guard — the chat endpoint is public and every request costs real
# Anthropic tokens. This caps the damage from scrapers / abuse at a few
# dollars a day.
#
# Known limits (accepted for a demo deployment):
#   - In-memory: resets when the container restarts (scale-to-zero does this
#     daily anyway), so it's "per container-lifetime up to the daily cap",
#     not a strict calendar-day ledger.
#   - Per-instance: with N Cloud Run instances the effective cap is N × budget.
#     Mitigated by setting the service's max-instances=1.
# ---------------------------------------------------------------------------
DAILY_BUDGET_USD = float(os.environ.get("CHAT_DAILY_BUDGET_USD", "3.0"))

_spend = {"date": "", "usd": 0.0}


def _check_budget():
    """Reset the counter on day rollover; reject when today's spend is over cap."""
    today = datetime.now(timezone.utc).date().isoformat()
    if _spend["date"] != today:
        _spend["date"] = today
        _spend["usd"] = 0.0
    if _spend["usd"] >= DAILY_BUDGET_USD:
        raise HTTPException(
            status_code=429,
            detail=(
                "Daily AI budget reached — the assistant is resting until "
                "midnight UTC. (This demo caps Claude API spend per day.)"
            ),
        )


def _record_spend(cost_usd: float):
    _spend["usd"] += cost_usd


def _get_bq() -> BQClient:
    global _bq
    if _bq is None:
        _bq = BQClient()
    return _bq


def _get_anthropic() -> Anthropic:
    global _anthropic
    if _anthropic is None:
        api_key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Neither CLAUDE_API_KEY nor ANTHROPIC_API_KEY set in environment."
            )
        _anthropic = Anthropic(api_key=api_key)
    return _anthropic


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    _check_budget()

    if not request.messages:
        raise HTTPException(status_code=400, detail="messages array is empty")
    if request.messages[-1].role != "user":
        raise HTTPException(
            status_code=400,
            detail="Last message must have role='user' (frontend bug?).",
        )

    user_message = request.messages[-1].content
    prior_messages = [
        {"role": m.role, "content": m.content} for m in request.messages[:-1]
    ]

    try:
        text, _, usage = run_agent(
            user_message=user_message,
            bq=_get_bq(),
            client=_get_anthropic(),
            messages=prior_messages,
        )
    except Exception as e:
        # Surface the error type + message; the frontend renders this in chat.
        raise HTTPException(
            status_code=500,
            detail=f"{type(e).__name__}: {e}",
        )

    _record_spend(usage["cost_usd"])

    return ChatResponse(
        message=Message(role="assistant", content=text),
        iterations=usage["iterations"],
        cost_usd=usage["cost_usd"],
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
    )
