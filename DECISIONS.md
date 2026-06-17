# Technical Decision Records — SmartWalletsTracker

> Lightweight ADRs (Architecture Decision Records). One entry per non-obvious
> engineering choice. Organized loosely from storage → ingestion → processing →
> ops. Append new entries; mark superseded ones `Superseded by ADR NNN`.
>
> **Writing convention:** every entry has Context / Decision / Reasons /
> Tradeoffs accepted. If a decision proves wrong later, add a
> `**Retrospective (N days later):**` block — don't rewrite history.

---

## ADR 001: BigQuery as source of truth (not Postgres, not local files)

**Status:** Accepted
**Date:** 2026-02-15 (approximate — pre-cloud migration)

**Context:**
Original MVP used local JSON files (`data/wallets_swap_data/*.json`) as
storage. Needed a persistent, queryable, cloud-accessible store for the
5-stage pipeline. Candidates: Postgres (Cloud SQL), DuckDB on GCS, BigQuery,
or staying with files + GCS.

**Decision:** BigQuery as the warehouse for all pipeline tables.

**Reasons:**
- **Analytical workload.** Every downstream query is an aggregation over
  thousands-to-millions of swap rows. Columnar storage wins over row-based.
- **No ops burden.** Serverless — no instance sizing, no patching,
  no backup scripts. Pipeline runs once a day; paying for an always-on
  Postgres instance is waste.
- **Partition + cluster built in.** `raw_swaps` partitioned by `tx_time` +
  clustered on `wallet_id` — predicate pushdown happens automatically when
  queries filter by date or wallet.
- **Free tier covers us.** 10 GB storage + 1 TB queries/month free; our
  workload uses ~2 GB and <50 GB queries.

**Tradeoffs accepted:**
- No enforced primary keys (BQ has only `NOT ENFORCED` hints). Leads to
  ADR 009 (application-layer dedup).
- Slower single-row lookups than Postgres. Irrelevant — we never do point
  lookups, only aggregations and batch reads.
- Less familiar to interviewers used to OLTP stacks. Mitigation: I can
  explain OLAP/OLTP tradeoff out loud.

---

## ADR 002: Cloud Run Jobs (not Cloud Functions, not a VM, not Cloud Composer)

**Status:** Accepted
**Date:** ~2026-03

**Context:**
Need to run `main.py` on a daily schedule in GCP. Candidates:
- Cloud Functions (event-driven FaaS)
- Cloud Run Jobs (batch containers)
- Cloud Run Services (always-on HTTP)
- Compute Engine VM (always-on, cron)
- Cloud Composer (managed Airflow)

**Decision:** Cloud Run Jobs + Cloud Scheduler.

**Reasons:**
- **Batch-shaped workload.** Pipeline runs once, takes ~5-10 min, exits.
  Jobs fits this exactly; Functions' 9-minute timeout would be a ceiling to
  worry about, Run Services would idle-bill while waiting for the 9am trigger.
- **Full container.** Can use the same Dockerfile locally and in cloud;
  no runtime-specific quirks (Functions has its own wrapping).
- **Cheap.** Scheduled invocations cost pennies/month. VM would be $20+/month
  for a process running 5 minutes a day.
- **Composer is overkill.** Airflow is a real DAG scheduler, but our pipeline
  is a 5-step sequential `python main.py` — Airflow adds ops overhead without
  benefit at this scale.

**Tradeoffs accepted:**
- No built-in retry policies; failures mean Scheduler marks the run failed
  and we investigate logs (fine for now).
- No DAG visualization. If the pipeline grows to 20+ stages with fan-out
  we'd revisit (Composer or Cloud Workflows).

---

## ADR 003: Helius API (not direct Solana RPC)

**Status:** Accepted
**Date:** early project

**Context:**
Need to fetch SWAP transactions for a list of wallets. Options:
- Direct Solana RPC (`getSignaturesForAddress` + `getTransaction`)
- Helius REST API (`/v0/addresses/{address}/transactions/?type=SWAP`)
- The Graph / Flipside
- Bitquery / Moralis

**Decision:** Helius REST API.

**Reasons:**
- **Pre-classified SWAPs.** Solana RPC returns all txs; classifying which are
  swaps requires parsing instruction data for every DEX program (Jupiter,
  Raydium, Orca, ...). Helius does this server-side and exposes `type=SWAP`.
- **Readable payloads.** Helius enriches with `events.swap`, `tokenTransfers`,
  human-readable fields. Raw Solana RPC returns base64 instruction data.
- **Free tier sufficient.** 100k credits/day; our daily workload is ~5k calls.
- **Pagination built in.** `before` cursor; no need to track slot boundaries
  manually.

**Tradeoffs accepted:**
- Vendor dependency on Helius. Mitigation: `raw_swaps` table preserves the
  raw payload — if we switch providers we can re-parse from stored data
  (partially; the parser would change).
- Helius's definition of "SWAP" differs from Dune's. Observed behavior:
  fewer swaps per wallet than Dune's count. Acceptable; documented in
  `INTERVIEW_TALKING_POINTS.md`.

---

## ADR 004: Adapter pattern for wallet ingestion sources

**Status:** Accepted
**Date:** 2026-04 (Phase 4)

**Context:**
Phase 4 added automated wallet discovery from Dune. Anticipated adding more
sources later (BirdEye, Arkham, Nansen). Two structures:
- Procedural: `ingest_from_dune()`, `ingest_from_birdeye()`, each called
  from `main.py`.
- Adapter pattern: abstract `SourceAdapter` with `fetch_candidates()`;
  orchestrator iterates over a list of adapters.

**Decision:** Adapter pattern. `lib/adapters/base.py` defines `SourceAdapter`;
each source is one file under `lib/adapters/`.

**Reasons:**
- **Open/Closed Principle.** Adding BirdEye = one new file + one line in
  `get_sources()`. No change to the orchestrator, filter logic, or schema.
- **Testability.** Each adapter has a single responsibility (fetch + shape
  into `Candidate` dataclass). Unit tests don't need to mock the orchestrator.
- **Named in interviews as the "ingestion pluggability story".** Concrete
  evidence that I think about extension before I need it — but not so
  preemptively that it costs me this project (each adapter is <80 lines).

**Tradeoffs accepted:**
- Small up-front cost (ABC, dataclass, `__init__.py`).
- Adds one layer of indirection when reading the code. Worth it.

---

## ADR 005: Raw + Refined data layering (`raw_swaps` → `analyzed_swaps`)

**Status:** Accepted
**Date:** cloud migration

**Context:**
Helius returns enriched but vendor-shaped JSON. Downstream analysis needs
a normalized schema (consistent across Jupiter / Raydium / OKX / etc).
Two options:
- Single table: parse on insert, store only the normalized form.
- Two tables: `raw_swaps` stores the original payload; `analyzed_swaps`
  stores the parsed form.

**Decision:** Two tables. `raw_swaps` is append-only, `analyzed_swaps` is
derived.

**Reasons:**
- **Replayable parsing.** When the parser changes (new DEX, bug fix in
  Jupiter handler), I can re-run it over `raw_swaps` without re-hitting
  Helius. This has already saved one debugging cycle.
- **Data lineage story.** Interviewers ask "where does the data come from?"
  Two-layer answer is cleaner: raw = vendor truth, refined = our interpretation.
- **VERSION field cooperation.** Parser has a `VERSION` integer; bumping it
  triggers re-parse of existing `raw_swaps`. Works only because raw is preserved.

**Tradeoffs accepted:**
- 2x storage for swap data. At <2 GB total, irrelevant on BigQuery pricing.
- One more table to think about in migrations. Worth it.

---

## ADR 006: Predicate pushdown for `analyze_wallets` (189s → 2s)

**Status:** Accepted
**Date:** during performance pass

**Context:**
Original `analyze_wallets.py` pulled ALL rows from `analyzed_swaps`, ALL
rows from `raw_swaps`, diffed in Python to find unprocessed records, then
parsed. Runtime grew linearly with warehouse size — 189 seconds at ~6000 rows.

**Decision:** Push the "what's unprocessed" filter to BigQuery:

```sql
SELECT * FROM raw_swaps r
WHERE NOT EXISTS (
  SELECT 1 FROM analyzed_swaps a
  WHERE a.signature = r.signature AND a.version = :current_version
)
```

**Reasons:**
- **BQ clusters on `signature` / `wallet_id`.** The anti-join is cheap at
  the storage engine level; Python dedup loaded 12k rows into memory to
  diff.
- **Runtime drops to O(new rows), not O(table size).** Currently ~20 rows/day
  after steady state; runtime is ~2s dominated by BQ round-trip.
- **This is the cleanest interview story in the project.** 95× speedup with a
  single SQL rewrite — textbook predicate pushdown.

**Tradeoffs accepted:**
- SQL anti-join less transparent than Python dedup for a new reader.
  Mitigation: comment block explaining the pattern.
- Tight coupling between parser VERSION bump and this query's correctness.
  Tested by manually bumping VERSION and watching all rows re-process.

---

## ADR 007: Batch queries in `filter_traders` (122 → 2 queries)

**Status:** Accepted
**Date:** during performance pass

**Context:**
Original `filter_traders.py` issued one BQ query per wallet (read its
analyzed_swaps), plus one per wallet to write classification → 122 queries
for 61 wallets. BQ's per-query overhead dominated runtime.

**Decision:** Two queries total:
1. One `SELECT * FROM analyzed_swaps WHERE wallet_id IN UNNEST(@ids)` to
   pull all data in one shot.
2. One batch `INSERT` of all classifications via `_load_rows`.

**Reasons:**
- **Per-query latency is ~300-500ms on BQ regardless of data size.** 122
  queries × 400ms = 49s of pure overhead. Two queries = <1s overhead.
- **Scales with wallet count.** At 220 wallets the old approach would be
  440 queries / ~3 minutes of overhead.
- **Pairs with ADR 006 as the "two performance wins" interview story.**
  Different bottleneck (per-query overhead), different fix (batching, not
  pushdown).

**Tradeoffs accepted:**
- Peak memory up slightly (all wallets' swaps in memory at once). ~30 MB for
  6000 rows — negligible.
- Classification logic now iterates in Python over all wallets instead of
  per-query. Same total Python work, different organization.

---

## ADR 008: Application-layer dedup (not DB-enforced primary keys)

**Status:** Accepted
**Date:** cloud migration

**Context:**
BigQuery's `PRIMARY KEY` is advertised but `NOT ENFORCED` — purely a hint to
the query planner. Need to prevent duplicate wallets / duplicate swaps /
duplicate classifications.

**Decision:** Enforce dedup in the Python ingestion layer:
- `wallets`: filter `c.address in fetch_existing_wallet_addresses()` before insert.
- `raw_swaps`: compare against `existing_signatures_for_wallet(wallet_id)`
  during Helius pagination; stop when we hit a known sig.
- `wallet_candidates`: `MERGE ON address AND source` — staging table + MERGE.
- `analyzed_swaps`: derived from `raw_swaps` via predicate pushdown; dedup
  is a byproduct (ADR 006).

**Reasons:**
- **No choice.** BQ can't do it DB-side. Ignoring dedup → duplicate rows
  that poison aggregations (wallet's total PnL doubles).
- **Helius "newest first" ordering enables early termination.** Rather than
  fetch 2000 txs and filter-in-Python, I stop pagination at the first known
  signature. Bounded API cost.
- **Set lookup is O(1).** `address in set` is cheap even at 10k+ wallets.

**Tradeoffs accepted:**
- If two ingest runs race, both could see "not in wallets" and both insert.
  Not a concern — pipeline is single-threaded daily.
- No database-level safety net. Caught one `insert_wallets_from_candidates`
  bug during development; since then, the dedup has held.

---

## ADR 009: Fail-soft ingestion (step 1), fail-hard downstream (steps 2-5)

**Status:** Accepted
**Date:** 2026-04 (Phase 4)

**Context:**
When Dune ingestion fails (API outage, quota exhausted, query archived),
should the whole pipeline abort, or should we keep processing the wallets
already tracked?

**Decision:** `ingest_wallets` is caught in `main.py` — if it fails, we
print a non-fatal banner and continue. Steps 2-5 are fail-hard; any of them
raising → `sys.exit(1)`.

**Reasons:**
- **Different failure semantics.** Ingestion failing means "no new wallets
  today" — annoying, not catastrophic. Collection/analysis/classification
  failing means "data is stale or wrong" — actionable immediately.
- **Don't punish the 220 tracked wallets because a new source is flaky.**
- **Cloud Scheduler treats non-zero exit as failure.** I want to be paged for
  real failures, not Dune hiccups.

**Tradeoffs accepted:**
- Silent ingestion failures require reading `ingestion_runs` table to spot.
  Mitigation: the orchestrator always logs a row regardless of status
  (try/except/finally), so monitoring is possible.
- "Silent" is relative — the banner is in Cloud Run logs.

---

## ADR 010: Two service accounts (`runner` + `scheduler`), least privilege

**Status:** Accepted
**Date:** cloud migration

**Context:**
The Cloud Run Job needs BQ + Secret Manager access. Cloud Scheduler needs
permission to trigger the Job. Could use one SA for both or separate them.

**Decision:** Two SAs.
- `smartwallets-runner@...`: roles on BQ (`dataEditor` on dataset, `jobUser`)
  + `secretAccessor` on specific secrets. Attached to the Job.
- `smartwallets-scheduler@...`: only `run.invoker` on the Job. Attached to
  the Scheduler.

**Reasons:**
- **Least privilege.** Scheduler has no business touching BQ data. Runner
  has no business triggering itself.
- **Blast radius.** If either SA is compromised, damage is bounded to its
  role. Classic security architecture.
- **Interview table stakes.** First question from a security-conscious
  reviewer: "how's your IAM?" Having two SAs with documented roles is the
  right answer.

**Tradeoffs accepted:**
- Two SAs to manage, two JSON keys (in dev). Minimal overhead given GCP's
  tooling.

---

## ADR 011: Secret Manager for API keys (not env vars in the image)

**Status:** Accepted
**Date:** cloud migration

**Context:**
`HELIUS_API_KEY`, `DUNE_API_KEY` need to be available to the container.
Options:
- Bake into the Docker image (easy but terrible).
- Cloud Run env vars in plain text (`--set-env-vars`) (better, but keys in
  `gcloud run jobs describe` output).
- Secret Manager with `--set-secrets` injection (proper).

**Decision:** Secret Manager, injected at runtime via `--set-secrets`.
App code reads them via `os.getenv(...)` — same interface as local `.env`.

**Reasons:**
- **Image is artifact — secrets aren't.** Anyone who pulls the image from
  Artifact Registry gets the image contents. Baking a secret in = distributing
  it to anyone with read access.
- **Rotation is free.** Add a new version in Secret Manager, Cloud Run picks
  it up on next run. No rebuild.
- **Audit logs.** Secret Manager logs who accessed what, when.
- **Code unchanged between local and cloud.** `.env` locally, Secret Manager
  on cloud, both end up in `os.getenv`. No code branches.

**Tradeoffs accepted:**
- Setup is ~5 lines of gcloud more than plain env vars. Paid once.

---

## ADR 012: Rate-limited daily promotion (20/day from 5000-wallet pool)

**Status:** Accepted
**Date:** 2026-04-19

**Context:**
The Dune query returns up to 5000 candidate wallets. Promoting all of them
to the `wallets` table at once would trigger Helius collection on all 5000,
blowing the free-tier quota (100k calls/day at ~2000 calls per wallet).

**Decision:** `DAILY_PROMOTION_LIMIT = 20`. Each ingest run takes the top-N
by volume from the pending pool; the rest stay in `wallet_candidates` with
`status='pending'` and get their turn on later runs.

**Reasons:**
- **Predictable Helius cost.** 20 new wallets × 2000 max-swaps = 40k calls
  worst case, within quota.
- **Natural prioritization.** Dune results come sorted by volume DESC, so
  we always process the biggest wallets first. If we ever need to cut the
  long tail, we lose the weakest signals, not the best.
- **Bounded failure blast.** If a new adapter version is buggy, we ruin 20
  wallets' data, not 5000.
- **Interview story.** "I could have dumped 5000 wallets into the pipeline,
  but I rate-limited for quota + blast-radius reasons. Here's the `[:20]` slice."

**Tradeoffs accepted:**
- 250 days to fully process a 5000-wallet pool. Fine — the project is
  long-running, new wallets appear in future Dune refreshes anyway.
- `wallet_candidates` pending count grows before it shrinks. Monitored but
  not an issue at current scale.

---

## ADR 013: `get_latest_result` instead of `run_query` on Dune free tier

**Status:** Accepted
**Date:** 2026-04-19

**Context:**
The Dune adapter originally used `get_latest_result(max_age_hours=12)` with
the intent that stale caches would auto-trigger re-execution. Switched to
`run_query` for deterministic freshness, which returned 400 Bad Request —
the `/execute` endpoint requires Dune's paid Analyst plan.

**Decision:** Revert to `get_latest_result(query_id)` without `max_age_hours`.
Dune query results are treated as a **manually-refreshed pool** — the user
re-runs the query in the Dune UI weekly/monthly. API only reads the cache,
0 credits per pipeline run.

**Reasons:**
- **Free tier constraint is real, not a bug to work around.** `/execute`
  simply isn't available.
- **The adapter is now fully deterministic.** No credit spend, no 400s,
  no "why is this intermittent."
- **Pairs with ADR 012.** Since we drip-feed 20/day, a weekly-refreshed pool
  is plenty fresh for a 250-day consumption timeline.

**Tradeoffs accepted:**
- New wallets appear at the cadence of manual Dune refreshes, not the
  pipeline's daily schedule. Explicit in the adapter's docstring.
- Can't auto-refresh without upgrading to paid Dune.

---

## ADR 014: Dune Analytics as the first wallet source

**Status:** Accepted
**Date:** 2026-04 (Phase 4)

**Context:**
Candidates for wallet discovery: Dune Analytics, BirdEye, Arkham, Nansen,
manual curation from Twitter.

**Decision:** Dune as the first (and currently only) source. Adapter pattern
(ADR 004) makes adding more cheap.

**Reasons:**
- **Free tier, no credit card.** 2500 credits/month suffices given ADR 013's
  cache-reading pattern.
- **SQL-native query authoring.** I write the filter in SQL, not bound by a
  provider's opinionated UI. Can encode "swing trader on majors/stables" as a
  Dune query directly.
- **Solana DEX coverage is strong.** `dex_solana.trades` is first-class.
- **Community queries exist to start from.**

**Tradeoffs accepted:**
- Dune's "trade" definition differs from Helius's "swap." Noted in
  INTERVIEW_TALKING_POINTS as a data-governance story — the two-layer
  ingestion (Dune → candidates; Helius → swap history) is intentional.
- If we later want BirdEye or Arkham, we write a new adapter. ADR 004 makes
  this a ~1-hour task.

---

## ADR 015: Reduce collection refresh frequency (not a signature-probe optimization)

**Status:** Accepted
**Date:** 2026-06-17

**Context:**
The daily Helius credit burn was unsustainable: ~140K credits on big-batch
days, used 779K of the 1M free-tier cap only 8 days into the cycle (projected
blackout ~Jun 19). The dominant method was the Enhanced Transactions API
(`TRANSACTION_HISTORY`), which `collect_traders_swaps` calls once per eligible
wallet. Probing two endpoints confirmed the cost asymmetry: the Enhanced API
took 10.8s on a quiet wallet (deep-scanning history for SWAP-type matches)
versus 94ms for a standard `getSignaturesForAddress` call — roughly a 100x
credit difference.

**Hypothesis (rejected):** Add a cheap "probe-first" step — call
`getSignaturesForAddress` (~1 credit), only call the Enhanced API when the
wallet's newest signature isn't one we already have. Expected to skip ~85% of
expensive calls.

**Measurement that killed the hypothesis:** A read-only dry-run over 30 real
eligible wallets showed only **7%** would be skipped, not 85%. A production
run's logs explained why: of 1529 wallets, **542 (35%) had genuinely new
swaps** and **987 (65%) had no new swaps** (the real waste) — but
`getSignaturesForAddress` returns *all* transaction types, while our dedup set
holds *swap* signatures only. These are active wallets that constantly do
non-swap transactions (transfers, approvals), so their newest signature is
almost never a known swap. The probe can't distinguish "new swap" from "new
anything," so it fires on ~93% of the wasteful wallets anyway, catching only
the ~7% that are fully dormant.

**Decision:** Drop the probe. Reduce the base refresh window in
`fetch_wallets_needing_collection` from 24h to 72h.

**Reasons:**
- **Targets the real driver.** The waste exists because every wallet is
  re-checked every 24h and 65% have nothing new. Checking every 72h cuts
  collection volume ~3x directly, independent of the type-filtering problem.
- **Right tradeoff for the product.** This is a smart-money tracker reasoning
  over months of history; a swap appearing 1-3 days later changes nothing.
- **Smooths the load.** A fixed 24h window made the eligible set oscillate
  (huge day / tiny day); spreading collection over 72h evens out daily credits.
- **One-line change, deployable before the cap.**

**Tradeoffs accepted:**
- New swaps surface up to ~3 days late. Acceptable here; revisit if real-time
  signals ever matter.
- The 65% "fetched but no new swaps" waste isn't *eliminated*, only hit ~3x
  less often. The precise fix (per-wallet adaptive backoff, or a swap-typed
  cheap probe) is deferred until after the Helius paid-tier upgrade + transfer
  ingestion — at which point the probe also stops mis-firing, because non-swap
  activity becomes data we actually want.

**Methodology note (the real lesson):** A dry-run + free log analysis
disproved an optimization I was confident in *before* deploying it or spending
credits to "verify" it. Same discipline as ADR 006 (measure before optimizing)
— here it prevented shipping a 7% fix dressed up as an 85% one.

---

## Template for future entries

```markdown
## ADR NNN: <decision in 5-8 words>

**Status:** Accepted | Superseded by ADR XXX | Rejected
**Date:** YYYY-MM-DD

**Context:**
<What situation forced this decision?>

**Decision:** <One sentence.>

**Reasons:**
- <Why this choice over alternatives.>
- <Non-obvious considerations.>

**Tradeoffs accepted:**
- <What we gave up.>
```
