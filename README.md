# SmartWalletsTracker

A daily-scheduled data pipeline that identifies **smart money wallets** on Solana by ingesting on-chain swap activity, parsing it into a normalized schema, and classifying wallets by trading behavior.

- **Runtime:** GCP Cloud Run Job, triggered by Cloud Scheduler
- **Storage:** BigQuery (raw + refined layers)
- **Sources:** Dune Analytics (wallet candidates), Helius (on-chain swaps), Binance (SOL price)

**Engineering highlights:**
9-table BigQuery schema · 7+ DEX swap parser (98.5% coverage) · **189s → 2s** query optimization via predicate pushdown · **N+1 query elimination (122 → 2)** via batched fetches · adapter pattern for pluggable wallet sources · two-SA defense-in-depth IAM · idempotent MERGE upserts · append-only classification snapshots

---

## Motivation

Smart-money labels on commercial services like Nansen and GMGN sit behind paywalls — but the data they're built from is fully public on-chain. Closing that gap is mostly a **data engineering** problem: pulling raw transactions across many DEXes, normalizing inconsistent payload shapes, deduping across sources, and classifying behavior at scale. This project takes that gap as the starting point.

---

## System Architecture

Where the project lives, who calls whom.

```
┌─────────────────────────────────────────────────────────────┐
│                    External Data Sources                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────────────────┐  │
│  │   Dune   │    │  Helius  │    │  Binance (SOL price) │  │
│  └────┬─────┘    └────┬─────┘    └──────────┬───────────┘  │
└───────┼───────────────┼─────────────────────┼──────────────┘
        │               │                     │
        ▼               ▼                     ▼
┌─────────────────────────────────────────────────────────────┐
│                      GCP Cloud Run Job                       │
│  ┌────────────────────────────────────────────────────┐    │
│  │  main.py (triggered daily by Cloud Scheduler)      │    │
│  │  1. ingest_wallets  → 2. fetch_sol_prices          │    │
│  │  3. collect_swaps   → 4. analyze  → 5. classify    │    │
│  └────────────────────────────────────────────────────┘    │
│       ▲                                                     │
│       │ API keys via                                        │
│  ┌─────────────┐                                            │
│  │  Secret     │  HELIUS_API_KEY, DUNE_API_KEY,             │
│  │  Manager    │  CLAUDE_API_KEY (for optional LLM stage)   │
│  └─────────────┘                                            │
└───────────┬─────────────────────────────────────────────────┘
            │ read/write
            ▼
┌─────────────────────────────────────────────────────────────┐
│                       BigQuery                               │
│   wallets  wallet_candidates  raw_swaps  analyzed_swaps      │
│   sol_prices  wallet_classifications  ingestion_runs ...     │
└─────────────────────────────────────────────────────────────┘
```

---

## Data Flow

How records move through the warehouse. Raw and refined layers are intentionally separated so parsing logic can be reworked without re-hitting external APIs.

```
  Dune API                    Helius API
     │                            │
     ▼                            ▼
[wallet_candidates]         [raw_swaps]      ← raw layer
     │ filter (CEX blacklist)    │             (original payloads, replayable)
     │ dedupe                    │ parse
     ▼                            ▼
  [wallets] ──────────────→ [analyzed_swaps]  ← refined layer
                                  │             (normalized, downstream-ready)
                                  │ aggregate
                                  ▼
                        [wallet_classifications]
                                  │
                                  ▼
                           (future: ML / LLM)
```

---

## Pipeline Stages

`main.py` runs five stages sequentially. Step 1 is fail-soft (an ingestion source outage shouldn't block processing of wallets we already track); steps 2–5 are fail-hard.

| # | Module | Responsibility | Writes to |
|---|--------|----------------|-----------|
| 1 | `ingest_wallets.py`     | Pull candidates from Dune, filter CEX, promote to tracked set | `wallet_candidates`, `wallets`, `ingestion_runs` |
| 2 | `fetch_sol_prices.py`   | Upsert daily SOL close price from Binance                     | `sol_prices`                                     |
| 3 | `collect_traders_swaps.py` | Pull new SWAP transactions from Helius per wallet          | `raw_swaps`                                      |
| 4 | `analyze_wallets.py`    | Parse raw payloads into normalized swap records               | `analyzed_swaps`                                 |
| 5 | `filter_traders.py`     | Classify wallets (smart / proxy / market-maker / ...)         | `wallet_classifications`                         |

---

## Adding a New Data Source

The ingestion layer uses an adapter pattern (Open/Closed). To add a new wallet source (e.g. BirdEye):

1. Create `lib/adapters/birdeye_adapter.py` implementing `SourceAdapter`
2. Register it in `ingest_wallets.get_sources()`
3. Done. No changes to the orchestrator, filter logic, or BigQuery schema.

---

## Local Development

```bash
# Setup
pip install -r requirements.txt

# Create a local .env at the repo root with:
#   HELIUS_API_KEY=<your key>
#   DUNE_API_KEY=<your key>
#   CLAUDE_API_KEY=<your key>   # optional, only for llm.py
# And authenticate with GCP:
#   gcloud auth application-default login

# Run the full pipeline locally (connects to the project's BigQuery dataset
# configured via GCP_PROJECT / dataset constants in lib/bq.py)
python main.py

# Run a single stage
python ingest_wallets.py
python fetch_sol_prices.py
```

---

## Deployment

```bash
make deploy    # builds Docker image, pushes to Artifact Registry, updates Cloud Run Job
```

See [DesignDoc.txt](DesignDoc.txt) and [DECISIONS.md](DECISIONS.md) for deeper design rationale and the architecture decision records.

---

## Repository Layout

```
.
├── main.py                     # pipeline orchestrator (Cloud Run entrypoint)
├── ingest_wallets.py           # Step 1 — wallet candidate ingestion
├── fetch_sol_prices.py         # Step 2 — SOL price backfill
├── collect_traders_swaps.py    # Step 3 — raw swap collection (Helius)
├── analyze_wallets.py          # Step 4 — swap parsing (Jupiter + token-transfer fallback)
├── filter_traders.py           # Step 5 — wallet classification
├── llm.py                      # optional / local-only — Claude API wallet narration
├── check_swaps.py              # local debugging helpers (raw vs analyzed reconciliation)
├── lib/
│   ├── bq.py                   # BigQuery helpers (upsert, MERGE, batch fetch, predicate pushdown)
│   ├── secrets.py              # Secret Manager / .env unified secret access
│   └── adapters/               # pluggable wallet sources
│       ├── base.py             # SourceAdapter ABC + Candidate dataclass
│       └── dune_adapter.py     # Dune implementation
├── infra/
│   ├── bigquery_schema.sql     # initial schema bootstrap
│   ├── migrate_to_bigquery.py  # one-shot file → BQ migration
│   └── migrations/             # versioned SQL migrations
├── scripts/
│   ├── smoke_test_credentials.py  # 4-service credential health check
│   └── test_dune_adapter.py
├── Dockerfile                  # python:3.11-slim base, layered for cache reuse
├── Makefile                    # deploy / build / run / logs shortcuts
├── DesignDoc.txt               # design rationale + perf stories
├── DECISIONS.md                # architecture decision records (ADRs)
└── requirements.txt
```
