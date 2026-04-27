"""
Abstract interface for wallet-discovery source adapters.

Every external data source (Dune, BirdEye, Arkham, ...) implements SourceAdapter.
The orchestrator (ingest_wallets.py) only talks to this interface — it doesn't
care which source it is. That's what makes sources pluggable.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Candidate:
    """One wallet candidate, normalized across sources."""
    address: str
    chain: str                                    # 'solana', 'ethereum', ...
    raw_metrics: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(ABC):
    """Every discovery source implements this."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """DB identifier, e.g. 'dune', 'birdeye'. Stored in wallet_candidates.source."""

    @property
    def source_query_id(self) -> str | None:
        """Provenance id (e.g. Dune query_id). None if source has no such concept."""
        return None

    @abstractmethod
    def fetch_candidates(self) -> list[Candidate]:
        """Pull candidates from the source. Raises on API failure (caller handles)."""
