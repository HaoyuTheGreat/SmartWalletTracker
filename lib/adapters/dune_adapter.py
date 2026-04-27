"""
Dune Analytics source adapter.

Reads the latest cached result of a Dune query via `get_latest_result` — 0 credits,
no execution. The Dune query is expected to return a large candidate pool
(~5000 wallets); the ingestion orchestrator rate-limits how many get promoted
to the `wallets` table per run (see DAILY_PROMOTION_LIMIT in ingest_wallets.py).

We do NOT trigger fresh executions from the API: on the free plan, the
`/execute` endpoint returns 400, and `get_latest_result(max_age_hours=N)`
relies on the same endpoint when the cache is stale. To refresh the candidate
pool, re-run the query manually in the Dune UI (e.g. once a week).
"""

import os

from dune_client.client import DuneClient

from .base import Candidate, SourceAdapter

DEFAULT_QUERY_ID = 7335552


class DuneAdapter(SourceAdapter):
    source_name = "dune"

    def __init__(self, query_id: int = DEFAULT_QUERY_ID):
        api_key = os.getenv("DUNE_API_KEY")
        if not api_key:
            raise RuntimeError("DUNE_API_KEY not set in environment")
        self._client = DuneClient(api_key)
        self._query_id = query_id

    @property
    def source_query_id(self) -> str:
        return str(self._query_id)

    def fetch_candidates(self) -> list[Candidate]:
        print(f"[dune] reading cached result for query {self._query_id} (0 credits)")
        response = self._client.get_latest_result(self._query_id)
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
