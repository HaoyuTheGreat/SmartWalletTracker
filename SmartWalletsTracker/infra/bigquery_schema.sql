-- =============================================================================
-- BigQuery Schema for WhaleTracker
-- Dataset: whale_tracker
-- Run via: bq query --use_legacy_sql=false < bigquery_schema.sql
-- =============================================================================

-- Create dataset (location: US for free-tier friendliness)
CREATE SCHEMA IF NOT EXISTS `smart-wallets-tracker.whale_tracker`
OPTIONS (
  location = "US",
  description = "WhaleTracker: on-chain swap data, analysis, and wallet classifications"
);

-- -----------------------------------------------------------------------------
-- Table 1: wallets (replaces wallets_list.json)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `smart-wallets-tracker.whale_tracker.wallets` (
  address           STRING NOT NULL,
  wallet_id         STRING NOT NULL,
  chain             STRING,
  source_token      STRING,
  source_token_mint STRING,
  discovered_at     TIMESTAMP,
  last_collected_at TIMESTAMP,
  collection_status STRING
)
OPTIONS (
  description = "Tracked wallet addresses and collection status"
);

-- -----------------------------------------------------------------------------
-- Table 2: raw_swaps (replaces data/wallets_swap_data/*.json)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `smart-wallets-tracker.whale_tracker.raw_swaps` (
  wallet_id     STRING NOT NULL,
  signature     STRING NOT NULL,
  tx_time       TIMESTAMP,
  tx_timestamp  INT64,
  source        STRING,
  raw_json      STRING,
  collected_at  TIMESTAMP
)
PARTITION BY DATE(tx_time)
CLUSTER BY wallet_id
OPTIONS (
  description = "Raw swap transactions from Helius, stored as JSON strings"
);

-- -----------------------------------------------------------------------------
-- Table 3: analyzed_swaps (replaces data/analyzed_swaps_data/*.json)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `smart-wallets-tracker.whale_tracker.analyzed_swaps` (
  wallet_id      STRING NOT NULL,
  signature      STRING NOT NULL,
  swap_time      TIMESTAMP NOT NULL,
  sol_price_usd  FLOAT64,
  sol_spent      FLOAT64,
  sol_received   FLOAT64,
  token_spent    ARRAY<STRUCT<
                    mint   STRING,
                    symbol STRING,
                    amount FLOAT64
                 >>,
  token_received ARRAY<STRUCT<
                    mint   STRING,
                    symbol STRING,
                    amount FLOAT64
                 >>,
  parser_version INT64,
  analyzed_at    TIMESTAMP
)
PARTITION BY DATE(swap_time)
CLUSTER BY wallet_id
OPTIONS (
  description = "Parsed swap transactions in unified schema across all DEX platforms"
);

-- -----------------------------------------------------------------------------
-- Table 4: sol_prices (replaces data/sol_price_history.json)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `smart-wallets-tracker.whale_tracker.sol_prices` (
  price_date DATE NOT NULL,
  price_usd  FLOAT64 NOT NULL,
  updated_at TIMESTAMP
)
OPTIONS (
  description = "Daily SOL/USD close prices from Binance"
);

-- -----------------------------------------------------------------------------
-- Table 5: wallet_classifications (replaces data/wallet_analysis.csv)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `smart-wallets-tracker.whale_tracker.wallet_classifications` (
  wallet_id          STRING NOT NULL,
  classified_at      TIMESTAMP NOT NULL,
  tags               ARRAY<STRING>,
  total_swaps        INT64,
  active_days        FLOAT64,
  daily_frequency    FLOAT64,
  proxy_pct          FLOAT64,
  buy_sell_ratio     FLOAT64,
  top_token_pct      FLOAT64,
  unique_tokens      INT64,
  closed_positions   INT64,
  inflated_positions INT64,
  win_rate           FLOAT64,
  total_pnl_sol      FLOAT64,
  avg_pnl_sol        FLOAT64
)
PARTITION BY DATE(classified_at)
CLUSTER BY wallet_id
OPTIONS (
  description = "Wallet classification results over time (append-only, never overwritten)"
);
