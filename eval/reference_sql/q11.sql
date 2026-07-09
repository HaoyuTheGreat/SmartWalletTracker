WITH latest AS (
  SELECT wallet_id, tags
  FROM `whale_tracker.wallet_classifications`
  QUALIFY ROW_NUMBER() OVER(PARTITION BY wallet_id ORDER BY classified_at DESC) = 1
)
SELECT
  COUNTIF('smart_candidate' in UNNEST(tags)) AS smart,
FROM latest