WITH latest AS (
  SELECT wallet_id, total_pnl_sol
  FROM `smart-wallets-tracker.whale_tracker.wallet_classifications`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY wallet_id ORDER BY classified_at DESC) = 1
)
SELECT wallet_id, total_pnl_sol
FROM latest
ORDER BY total_pnl_sol DESC
LIMIT 10