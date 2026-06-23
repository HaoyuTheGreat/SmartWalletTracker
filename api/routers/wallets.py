"""
Wallets endpoints — list (with tag filter + sort) and detail.

Joins the latest snapshot from `wallet_classifications` with the `wallets`
table so each row carries both identity (address) and classification metrics.
"""

import time

from fastapi import APIRouter, Query, Response
from google.cloud import bigquery

from api.schemas import WalletPage, WalletSummary
from lib.bq import client, _table

router = APIRouter(prefix="/api/wallets", tags=["wallets"])


VALID_SORTS = {
    "total_pnl_sol",
    "win_rate",
    "total_swaps",
    "active_days",
    "classified_at",
}

# The wallet list changes at most once/day (after the pipeline). Cache results
# in-process so repeat opens / sort toggles / pagination don't re-hit BigQuery
# (~1s each). TTL-bounded so a fresh classification still surfaces within minutes.
# Paired with Cloud Run min-instances=1, the warm instance keeps this cache hot.
_LIST_CACHE: dict[tuple, tuple[float, WalletPage]] = {}
_LIST_CACHE_TTL = 300  # seconds


@router.get("", response_model=WalletPage)
def list_wallets(
    response: Response,
    tag: str | None = Query(
        default=None,
        description="Filter wallets whose latest classification has this tag (e.g. 'smart_candidate').",
    ),
    sort: str = Query(
        default="total_pnl_sol",
        description="Field to sort by. One of: total_pnl_sol, win_rate, total_swaps, active_days, classified_at.",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """
    Return one page of wallets joined with their latest classification snapshot,
    plus the total row count for the current filter (so the UI can show
    "Page X of Y").

    Implementation: the inner CTE applies the tag filter ONCE, then
    `COUNT(*) OVER ()` adds the matching-row total to every page row — a
    single BQ scan returns both the page and the total. The total is
    independent of LIMIT/OFFSET (it counts pre-pagination).

    Latest snapshot per wallet is computed via QUALIFY ROW_NUMBER() — gives one
    row per wallet using the most recent `classified_at`. Wallets that have
    never been classified are excluded.
    """
    if sort not in VALID_SORTS:
        sort = "total_pnl_sol"

    response.headers["Cache-Control"] = f"public, max-age={_LIST_CACHE_TTL}"
    cache_key = (tag, sort, limit, offset)
    cached = _LIST_CACHE.get(cache_key)
    if cached and (time.monotonic() - cached[0]) < _LIST_CACHE_TTL:
        return cached[1]

    query = f"""
        WITH latest AS (
          SELECT *
          FROM `{_table('wallet_classifications')}`
          QUALIFY ROW_NUMBER() OVER (PARTITION BY wallet_id ORDER BY classified_at DESC) = 1
        ),
        filtered AS (
          SELECT
            w.wallet_id,
            w.address,
            l.tags,
            l.win_rate,
            l.total_pnl_sol,
            l.total_swaps,
            l.classified_at,
            COUNT(*) OVER () AS total_count
          FROM `{_table('wallets')}` w
          JOIN latest l USING (wallet_id)
          WHERE (@tag IS NULL OR @tag IN UNNEST(l.tags))
        )
        SELECT * FROM filtered
        ORDER BY {sort} DESC NULLS LAST
        LIMIT @limit OFFSET @offset
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("tag", "STRING", tag),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
            bigquery.ScalarQueryParameter("offset", "INT64", offset),
        ]
    )
    rows = []
    total = 0
    for row in client().query(query, job_config=job_config).result():
        data = dict(row)
        total = data.pop("total_count")
        rows.append(WalletSummary(**data))
    page = WalletPage(rows=rows, total=total)
    _LIST_CACHE[cache_key] = (time.monotonic(), page)
    return page
