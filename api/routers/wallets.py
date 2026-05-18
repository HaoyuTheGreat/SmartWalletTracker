"""
Wallets endpoints — list (with tag filter + sort) and detail.

Joins the latest snapshot from `wallet_classifications` with the `wallets`
table so each row carries both identity (address) and classification metrics.
"""

from fastapi import APIRouter, Query
from google.cloud import bigquery

from api.schemas import WalletSummary
from lib.bq import client, _table

router = APIRouter(prefix="/api/wallets", tags=["wallets"])


VALID_SORTS = {
    "total_pnl_sol",
    "win_rate",
    "total_swaps",
    "active_days",
    "classified_at",
}


@router.get("", response_model=list[WalletSummary])
def list_wallets(
    tag: str | None = Query(
        default=None,
        description="Filter wallets whose latest classification has this tag (e.g. 'smart_candidate').",
    ),
    sort: str = Query(
        default="total_pnl_sol",
        description="Field to sort by. One of: total_pnl_sol, win_rate, total_swaps, active_days, classified_at.",
    ),
    limit: int = Query(default=50, ge=1, le=500),
):
    """
    Return wallets joined with their latest classification snapshot.

    Latest snapshot per wallet is computed via QUALIFY ROW_NUMBER() — gives one
    row per wallet using the most recent `classified_at`. Wallets that have
    never been classified are excluded (LEFT JOIN would surface them but they
    can't be filtered or ranked meaningfully without metrics).
    """
    if sort not in VALID_SORTS:
        sort = "total_pnl_sol"

    query = f"""
        WITH latest AS (
          SELECT *
          FROM `{_table('wallet_classifications')}`
          QUALIFY ROW_NUMBER() OVER (PARTITION BY wallet_id ORDER BY classified_at DESC) = 1
        )
        SELECT
          w.wallet_id,
          w.address,
          l.tags,
          l.win_rate,
          l.total_pnl_sol,
          l.total_swaps,
          l.classified_at
        FROM `{_table('wallets')}` w
        JOIN latest l USING (wallet_id)
        WHERE (@tag IS NULL OR @tag IN UNNEST(l.tags))
        ORDER BY {sort} DESC NULLS LAST
        LIMIT @limit
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("tag", "STRING", tag),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]
    )
    return [
        WalletSummary(**dict(row))
        for row in client().query(query, job_config=job_config).result()
    ]
