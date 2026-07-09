"""
Dashboard stats endpoints — high-level project metrics for the frontend.
"""

from fastapi import APIRouter

from api.schemas import DashboardStats
from lib.bq import client, _table

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/dashboard", response_model=DashboardStats)
def dashboard():
    """One-shot snapshot of project scale, used by the frontend dashboard header."""
    query = f"""
        SELECT
          (SELECT COUNT(*) FROM `{_table('wallet_candidates')}`) AS candidates_scanned,
          (SELECT COUNT(*) FROM `{_table('wallets')}`
             WHERE collection_status = 'ok') AS wallets_tracked,
          (SELECT COUNT(DISTINCT wallet_id) FROM `{_table('wallet_classifications')}`) AS wallets_classified,
          (
            SELECT COUNT(*) FROM (
              SELECT wallet_id, tags
              FROM `{_table('wallet_classifications')}`
              QUALIFY ROW_NUMBER() OVER (PARTITION BY wallet_id ORDER BY classified_at DESC) = 1
            )
            WHERE 'smart_candidate' IN UNNEST(tags)
          ) AS smart_candidates,
          (SELECT COUNT(*) FROM `{_table('raw_swaps')}`) AS total_swaps,
          (SELECT COUNT(DISTINCT signature) FROM `{_table('raw_swaps')}`) AS unique_signatures,
          (SELECT ROUND(SUM(LENGTH(raw_json))/1024/1024, 1) FROM `{_table('raw_swaps')}`) AS raw_data_size_mb,
          (SELECT MAX(classified_at) FROM `{_table('wallet_classifications')}`) AS latest_classification_at,
          (SELECT MAX(tx_time) FROM `{_table('raw_swaps')}`) AS latest_swap_at
    """
    row = next(iter(client().query(query).result()))
    return DashboardStats(**dict(row))
