"""
Dune Analytics source adapter.

Uses `get_latest_result` which is 0 credits when a cached result exists (<= max_age_hours).
Falls back to executing the query fresh if cache is stale.

Free tier: 2500 credits/month. A fresh execution of our wallet-candidate query
consumes ~10 credits — running daily uses 300/month (~12% of quota).
"""

import os

from dune_client.client import DuneClient

from .base import Candidate, SourceAdapter

DEFAULT_QUERY_ID = 7335552


class DuneAdapter(SourceAdapter):
    source_name = "dune"

    def __init__(
        self,
        query_id: int = DEFAULT_QUERY_ID,
        max_age_hours: int = 24,
    ):
        api_key = os.getenv("DUNE_API_KEY")
        if not api_key:
            raise RuntimeError("DUNE_API_KEY not set in environment")
        self._client = DuneClient(api_key)
        self._query_id = query_id
        self._max_age_hours = max_age_hours

    @property
    def source_query_id(self) -> str:
        return str(self._query_id)

    def fetch_candidates(self) -> list[Candidate]:
        response = self._client.get_latest_result(
            self._query_id,
            max_age_hours=self._max_age_hours,
        )
        rows = response.result.rows if response.result else []

        candidates: list[Candidate] = []
        for row in rows:
            address = row.get("wallet_address")
            if not address:
                continue
            candidates.append(Candidate(
                address=address,
                chain="solana",
                raw_metrics={
                    "trade_count":        row.get("trade_count"),
                    "active_days":        row.get("active_days"),
                    "total_volume_usd":   row.get("total_volume_usd"),
                    "avg_trade_size_usd": row.get("avg_trade_size_usd"),
                    "first_trade_at":     row.get("first_trade_at"),
                    "last_trade_at":      row.get("last_trade_at"),
                },
            ))
        return candidates
