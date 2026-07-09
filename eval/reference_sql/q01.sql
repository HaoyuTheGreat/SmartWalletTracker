--q01:
SELECT  COUNT(collection_status) 
FROM `smart-wallets-tracker.whale_tracker.wallets` 
WHERE collection_status = 'ok' 