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

from anthropic import Anthropic
from fastapi import APIRouter, HTTPException

from api.agent import BQClient, run_agent
from api.schemas import ChatRequest, ChatResponse, Message

router = APIRouter(prefix="/api", tags=["chat"])

_bq: BQClient | None = None
_anthropic: Anthropic | None = None


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

    return ChatResponse(
        message=Message(role="assistant", content=text),
        iterations=usage["iterations"],
        cost_usd=usage["cost_usd"],
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
    )
