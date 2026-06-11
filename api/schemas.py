"""
Pydantic response models for the REST API.

These define the shape of every JSON response the API returns. FastAPI uses
them to validate outgoing data, auto-generate OpenAPI docs (visible at /docs),
and feed the frontend a typed contract.
"""

from datetime import datetime

from pydantic import BaseModel


class DashboardStats(BaseModel):
    """High-level project metrics for the frontend dashboard header."""

    candidates_scanned: int
    wallets_tracked: int
    wallets_classified: int
    smart_candidates: int
    total_swaps: int
    unique_signatures: int
    raw_data_size_mb: float | None
    latest_classification_at: datetime | None
    latest_swap_at: datetime | None


class WalletSummary(BaseModel):
    """One row in the wallets list — minimal fields for the table view."""

    wallet_id: str
    address: str
    tags: list[str]
    win_rate: float | None
    total_pnl_sol: float | None
    total_swaps: int | None
    classified_at: datetime | None


class WalletPage(BaseModel):
    """Paginated wallets response — `rows` is the current page, `total` is
    the count of rows matching the filter (independent of limit/offset) so
    the frontend can render "Page X of Y" without a second round-trip."""

    rows: list[WalletSummary]
    total: int


class Message(BaseModel):
    """One message in a chat conversation. Content is plain text only —
    we intentionally don't round-trip Anthropic content blocks (tool_use /
    tool_result) through the frontend; only final assistant text is preserved
    across turns. Multi-turn pronoun resolution still works because the text
    of prior answers is retained."""

    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    """POST /api/chat — full conversation history from the client.

    Last entry must be the user's new question. Prior entries are the running
    transcript (client-side state, simpler than server sessions for MVP).
    """

    messages: list[Message]


class ChatResponse(BaseModel):
    """Response from POST /api/chat — assistant's new message + usage stats."""

    message: Message
    iterations: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
