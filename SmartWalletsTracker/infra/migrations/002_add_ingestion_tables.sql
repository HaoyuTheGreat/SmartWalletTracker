-- =============================================================================
-- Migration 002: Wallet Auto-Ingestion Tables
-- Dataset: smart-wallets-tracker.whale_tracker
-- Run via: bq query --use_legacy_sql=false < 002_add_ingestion_tables.sql
--
-- Adds infrastructure to ingest wallet candidates from external sources
-- (Dune Analytics first, later BirdEye / Arkham / etc.) into the pipeline.
--
-- Flow: external source -> wallet_candidates (buffer) -> filter -> wallets
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Table: wallet_candidates
-- Buffer pool: raw candidates fetched from external sources, before filtering.
-- One row per (address, source) pair — same wallet from two sources = two rows.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `smart-wallets-tracker.whale_tracker.wallet_candidates` (
  address          STRING NOT NULL,
  chain            STRING NOT NULL,
  source           STRING NOT NULL,             -- 'dune' | 'birdeye' | 'arkham' | ...
  source_query_id  STRING,                      -- e.g. Dune query_id, for provenance
  discovered_at    TIMESTAMP NOT NULL,
  raw_metrics      JSON,                        -- full raw row from source (trade_count, volume, etc.)
  status           STRING NOT NULL,             -- 'pending' | 'promoted' | 'filtered_out'
  filter_reason    STRING,                      -- why filtered out (e.g. 'known_cex', 'is_contract')
  PRIMARY KEY (address, source) NOT ENFORCED
)
PARTITION BY DATE(discovered_at)
CLUSTER BY address
OPTIONS (
  description = "Raw wallet candidates from external sources, before filter_traders"
);

-- -----------------------------------------------------------------------------
-- Table: wallet_sources
-- Provenance: which source(s) flagged a given wallet, and how many times.
-- One wallet can appear in multiple sources = multiple rows.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `smart-wallets-tracker.whale_tracker.wallet_sources` (
  address       STRING NOT NULL,
  source        STRING NOT NULL,
  first_seen_at TIMESTAMP NOT NULL,
  last_seen_at  TIMESTAMP NOT NULL,
  seen_count    INT64 NOT NULL,                 -- how many ingestion runs flagged this wallet
  PRIMARY KEY (address, source) NOT ENFORCED
)
OPTIONS (
  description = "Multi-source provenance: which source(s) saw this wallet"
);

-- -----------------------------------------------------------------------------
-- Table: ingestion_runs
-- Observability: one row per ingestion attempt (daily cron run per source).
-- Used to debug "why did yesterday only ingest 50 wallets?" type questions.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `smart-wallets-tracker.whale_tracker.ingestion_runs` (
  run_id                STRING NOT NULL,        -- uuid per run
  source                STRING NOT NULL,
  started_at            TIMESTAMP NOT NULL,
  finished_at           TIMESTAMP,
  status                STRING,                 -- 'success' | 'failed' | 'partial'
  candidates_fetched    INT64,                  -- total rows returned by source
  candidates_new        INT64,                  -- new candidates after dedup
  promoted_to_wallets   INT64,                  -- passed filter and reached wallets table
  credits_used          INT64,                  -- API credits consumed (Dune etc.)
  error_message         STRING
)
PARTITION BY DATE(started_at)
OPTIONS (
  description = "One row per ingestion run — observability + debugging"
);

-- -----------------------------------------------------------------------------
-- Table: exchange_wallets
-- Static blacklist of known CEX / market-maker deposit addresses.
-- Used by filter_traders to reject candidates that are really exchange hot wallets.
-- Populate manually (or via future seed script).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `smart-wallets-tracker.whale_tracker.exchange_wallets` (
  address       STRING NOT NULL,
  exchange_name STRING,                         -- 'Binance' | 'Coinbase' | 'Jupiter' | ...
  chain         STRING NOT NULL,
  added_at      TIMESTAMP NOT NULL
)
OPTIONS (
  description = "Known CEX / market-maker deposit addresses — filter blacklist"
);

-- -----------------------------------------------------------------------------
-- ALTER: wallets
-- Add lifecycle columns so we can track WHY a wallet is/isn't active.
-- Existing rows will have NULL in these columns — backfill via UPDATE if needed.
-- -----------------------------------------------------------------------------
ALTER TABLE `smart-wallets-tracker.whale_tracker.wallets`
  ADD COLUMN IF NOT EXISTS status         STRING,    -- 'active' | 'filtered_out' | 'archived'
  ADD COLUMN IF NOT EXISTS filter_reason  STRING,    -- inherited from wallet_candidates
  ADD COLUMN IF NOT EXISTS promoted_from  STRING;    -- 'dune' | 'manual' | 'birdeye' | ...

-- Backfill: mark all existing 61 wallets as 'active' + 'manual'
UPDATE `smart-wallets-tracker.whale_tracker.wallets`
SET status = 'active', promoted_from = 'manual'
WHERE status IS NULL;
