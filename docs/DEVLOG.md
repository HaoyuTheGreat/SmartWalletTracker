# DEVLOG — SmartWalletsTracker

> Problems hit while building this project and how each was solved, in chronological order.
> Read top to bottom = the whole build/optimization journey.
>
> Format per entry: **Problem (symptom) → Cause → Fix → Result** (+ **Lesson** where it matters).
> New entries are appended at the bottom. ★ marks the ones worth reading first.
>
> Companion docs:
> - **DEVLOG.md** (this file) = what broke and how I fixed it (the journey)
> - **DECISIONS.md** = why X over Y (architecture decisions)
> - **RECAP.md** = what the system looks like and how it runs today (current snapshot)

## Index

| # | Date | What happened |
|---|------|---------------|
| 01 | 2026-02~03 | Local JSON files → BigQuery |
| 02 | 2026-05 | Dune and Helius disagree on what a "swap" is |
| 03 ★ | 2026-05 | analyze_wallets 189s → 2s (predicate pushdown) |
| 04 | 2026-05 | filter_traders N+1: 122 → 2 queries |
| 05 | 2026-05 | Dependency conflict: dune-client vs requests |
| 06 | 2026-05-13 | filter_traders hit the 3600s job timeout |
| 07 | 2026-05-18~20 | A batch of frontend visual/style bugs |
| 08 | 2026-05-19 | win_rate showing 4000% |
| 09 | 2026-05-22 | Vercel ↔ Cloud Run CORS |
| 10 | 2026-05 | React setState-in-effect (cascading renders) |
| 11 | 2026-06-10 | Homepage numbers were hardcoded |
| 12 ★ | 2026-06-10~11 | Pipeline OOM at 8Gi (Steps 3 + 5) |
| 13 | 2026-06-11 | A public chat endpoint = a money leak |
| 14 ★ | 2026-06-17 | Helius credit waste: a dry-run disproves my own optimization |
| 15 | 2026-06-19 | Step 4 (analyze) unbounded fetch, chunked before backfill |
| 16 ★ | 2026-06-19 | Helius host retired + a silent failure that hid it for a day |

---

## 01 · 2026-02~03 — Local JSON files → BigQuery

**Problem**: Early data lived in local `data/*.json`. Once on Cloud Run (stateless), files written by one run were gone the next; small-file IO was slow, and multi-dimensional queries ("total volume across all wallets over the last 30 days") were impossible.

**Cause**: Local files don't fit a stateless cloud runtime or an analytical (OLAP) workload.

**Fix**: A one-off `infra/migrate_to_bigquery.py` loaded the local JSON into BigQuery; from then on every pipeline table lands in BQ (typed schema + partition/cluster + SQL-queryable).

**Result**: Durable, concurrent, aggregatable data. This is where the project went from "script" to "system."

---

## 02 · 2026-05 — Dune and Helius disagree on what a "swap" is

**Problem**: Dune queries flagged "high-frequency" wallets (trade_count 20–500), but pulling them from Helius gave only ~31 swaps/wallet on average — 3–5× fewer. Looked like a bug at first.

**Cause**: Dune `dex_solana.trades` and Helius `type=SWAP` are two companies independently indexing the same chain, with different definitions of what counts as a swap (Jupiter 2-hop routes, Pump.fun bonding curves, LP add/remove all classified differently). Key realization: **"is this a swap" is a classifier's subjective call, not a property of the chain.**

**Fix**: Architecturally keep `raw_swaps` (Helius raw JSON) + `analyzed_swaps` (parsed layer) + `parser_version`. If "swap" is ever redefined, re-run off the raw layer instead of paying to re-fetch.

**Result**: Cross-source disagreement went from "bug" to "a known difference absorbed by the architecture." Interview story: data governance / late binding over early coupling.

---

## 03 · 2026-05 — analyze_wallets 189s → 2s (predicate pushdown) ★

**Problem**: Every re-run of `analyze_wallets.py` took 189s to decide "which raw_swaps aren't parsed yet," even when there was no new data.

**Cause**: The old approach pulled all raw_swaps + analyzed_swaps into Python and diffed them in memory — hundreds of MB over the wire + JSON parsing, with Python as the bottleneck.

**Fix**: Push the filter down to BigQuery (anti-join: `LEFT JOIN ... WHERE a.signature IS NULL`); BQ computes the difference, Python only receives rows that actually need work.

**Result**: 189s → 2s (~95×). Term: predicate pushdown. Idle runs now exit almost instantly. See DECISIONS ADR 006.

---

## 04 · 2026-05 — filter_traders N+1: 122 → 2 queries

**Problem**: `filter_traders.py` computed a profile per wallet with 2 queries each (raw + analyzed) — 61 wallets = 122 queries, each with ~500ms slot startup → 60+s.

**Cause**: N+1 query pattern — the abstraction hid the IO cost.

**Fix**: Two `GROUP BY wallet_id` bulk queries, bucketed by wallet_id in Python memory.

**Result**: 60+s → a few seconds. Term: N+1 elimination / batch loading. See DECISIONS ADR 007.

---

## 05 · 2026-05 — Dependency conflict: dune-client vs requests

**Problem**: `make deploy` Docker build broke: `dune-client 1.10.0 depends on requests~=2.32.5` but we'd pinned `requests==2.33.1`.

**Cause**: Strict pins (`==`) collide with version constraints when adding new dependencies.

**Fix**: Dropped `requests` to `2.32.5`, satisfying both google-cloud-bigquery (`>=2.21,<3`) and dune-client (`~=2.32.5`).

**Result**: Build passed. Lesson: exact pins buy reproducibility at the cost of manual conflict resolution; at scale, move to pip-tools/uv lock files.

---

## 06 · 2026-05-13 — filter_traders hit the 3600s job timeout

**Problem**: The Cloud Run Job died on the 1-hour timeout.

**Cause**: `filter_traders` pulled all 1.5GB+ of raw_swaps into Python every run, even for wallets whose data hadn't changed.

**Fix**: Made it incremental — added `fetch_wallets_needing_classification` (another anti-join) to process only wallets with new data since last classification; also raised the job task-timeout to 7200s.

**Result**: From "rescan everything daily" to "scan only what changed." (Note: this is the prequel to the OOM incident — the same file later blew up anyway, see #12.)

---

## 07 · 2026-05-18~20 — A batch of frontend visual/style bugs (merged)

**Problem + Fix** (small traps during the Day 4–5 frontend sprint):
- **Shooting stars looked like comets, not digit strings**: glyph spacing scaled with speed → overlap at low speed. Switched to fixed pixel spacing (22px) along the unit direction vector.
- **Stars invisible (z-stacking)**: `main`'s `bg-black` covered the `-z-10` canvas. Removed bg-black, made the gradient layer `fixed`.
- **Tailwind v4 syntax**: `bg-gradient-*` errored → use `bg-linear-*` (v4 renamed it).
- **Invisible tag text**: `<html>` had no `dark` class → shadcn fell back to the light theme → dark text on a black background. Added `dark` to html.

**Lesson**: Breaking changes in framework upgrades (Tailwind v4 / Next 16 / React 19) are a common source of frontend bugs — the error message usually points at the new syntax.

---

## 08 · 2026-05-19 — win_rate showing 4000%

**Problem**: The Explore table showed win rates like 4000%, 5560%.

**Cause**: Inconsistent data semantics — the pipeline (`filter_traders.py`) stored win_rate as a **0–100 percentage** (`wins/total*100`), but the frontend `formatPercent` multiplied by 100 again, treating it as a 0–1 fraction.

**Fix**: Removed the frontend `*100`, added a comment that the pipeline stores 0–100.

**Result**: Correct display. Lesson: cross-layer data semantics (0–1 vs 0–100) must be made explicit — a classic source of silent bugs.

---

## 09 · 2026-05-22 — Vercel frontend couldn't reach the Cloud Run backend (CORS)

**Problem**: After deploying to Vercel, the homepage worked but the Explore table silently failed to load (no visible error, empty data).

**Cause**: Browser same-origin policy — the Cloud Run API's `CORS_ORIGINS` only allowed localhost, not the Vercel domain, so the browser blocked the request (visible as a CORS error in DevTools).

**Fix**: One gcloud command added the Vercel domain to the `CORS_ORIGINS` env var; the service auto-restarted.

**Result**: Fixed in 3 minutes. Lesson: CORS_ORIGINS should have been per-environment config from the start, not a localhost default + a manual prod patch.

---

## 10 · 2026-05 — React: synchronous setState inside an effect (cascading renders)

**Problem**: ESLint flagged `react-hooks/set-state-in-effect` — calling setState synchronously in useEffect triggers cascading re-renders. Appeared on the Explore page (loading state) and ChatPanel (restoring a conversation from sessionStorage).

**Cause**: Calling setState directly in an effect body is a misuse of effects.

**Fix**: ① Explore page — derive loading state from a params-stamped result (no setState in effect); ② ChatPanel — read sessionStorage with a lazy `useState(() => ...)` initializer instead of setState in an effect.

**Result**: Lint clean + no cascading renders. Lesson: understanding React 18+ effect semantics is a frontend-interview plus.

---

## 11 · 2026-06-10 — Homepage numbers were hardcoded, didn't track the data

**Problem**: Homepage "5,000 candidates / 111 trading smart" was hardcoded in JSX; the real data had long since moved (5,018 / 165+), so the page lagged reality by a month and the "Live" green dot was a lie.

**Cause**: Numbers were hardcoded early for a demo and never wired to the real API.

**Fix**: Homepage became a server component fetching `/api/stats/dashboard` + ISR 1h cache; the backend stats endpoint gained a `candidates_scanned` field; it falls back to a static snapshot if the API is down (never a blank page); the status dot became honest (classification stalls → yellow "pipeline delayed").

**Result**: Every number is live, auto-refreshing hourly. The status dot became free pipeline-health monitoring — and later (see #16) is what caught a production outage.

---

## 12 · 2026-06-10~11 — Pipeline OOM at 8Gi (Steps 3 + 5) ★

**Problem**: Two days in a row, the daily run was killed by signal 9 ("memory limit reached") — 8Gi still blew up. Dashboard data went stale.

**Cause** (three layers stacked):
1. **Step 3 collect**: triple per-wallet overhead (N+1 signature queries + one load job per wallet + one UPDATE per wallet), growing linearly as wallets went 61 → 1400.
2. **Step 5 filter** (the real bomb): pulled all pending wallets' raw_json into Python at once — ~3.7GB of JSON → 8–10GB of Python objects on a full backlog.
3. **Deadlock**: classification results were written only at the very end → on OOM nothing was written → the backlog carried over unchanged → the next day faced the same huge backlog and died again, never self-healing.

**Fix** (bounded by construction, not more memory):
- Step 3: one signature snapshot query (kills the N+1) + a 5000-row buffer flush + batched status UPDATEs + timely `del` of large objects.
- Step 5: 25-wallet batches + **persist each batch** (progress survives crashes, the backlog converges monotonically, breaking the deadlock).

**Result**: Stress test passed — 1430 wallets + 1029 classifications / 8Gi / 62 min. Full post-mortem in RECAP.md Step 3. Lesson: "the bug I thought I had (per-wallet overhead) hid a worse one (unbounded fetch + deadlock)" — let the logs tell the whole story before fixing.

---

## 13 · 2026-06-11 — A public chat endpoint = a money leak

**Problem**: Shipping QuerySmith chat to the web meant `POST /api/chat` was public on Cloud Run, every request spending real Anthropic tokens — scrapers/abuse could torch the bill.

**Cause**: The 6 defense layers protected BigQuery, not the Anthropic bill.

**Fix**: Backend added an in-memory daily budget gate (`CHAT_DAILY_BUDGET_USD`, default $3), returning 429 + a friendly message over budget; service set to `max-instances=1` so the counter is meaningful. Known limits (in-memory counter, per-instance) documented in code.

**Result**: Zero-cost verification (budget=0 → 429) + production verified.

---

## 14 · 2026-06-17 — Helius credit waste: using a dry-run to disprove my own optimization ★

**Problem**: Helius credits were burning through the free tier (779K/1M in 8 days). Diagnosis: collect hit the expensive Enhanced API (~100 credits) for every wallet, while ~65% of wallets had no new swaps at all.

**My hypothesis**: add a cheap probe (`getSignaturesForAddress`, ~1 credit) to check for new activity first, only calling the expensive endpoint when there is any. **I was confident it would save ~85%.**

**Key move**: Didn't deploy. Wrote a read-only dry-run that ran only the cheap probe against 30 real wallets and estimated how many it would skip — without spending a single expensive credit.

**Result disproved → pivot**: only 7% savings, not 85%. Logs revealed why: `getSignaturesForAddress` returns **all-type** signatures, but the dedup set holds only **swap** signatures; active wallets have non-swap activity daily → the probe can't tell "new swap" from "new anything," misfiring 93% of the time. **Dropped the probe; changed the refresh window 24h → 48h instead** (a direct ~2× cut, unrelated to the type problem). See DECISIONS ADR 015.

**Lesson (the big one)**: a zero-cost dry-run disproved an optimization I was confident in, before deploying and before spending credits to "verify." "Measure before optimizing" isn't just baselining — it includes **disproving your estimate of an optimization's payoff in the cheapest way possible.**

---

## 15 · 2026-06-19 — Step 4 (analyze) unbounded fetch, chunked before backfill

**Problem**: `analyze_wallets.py` loaded all unanalyzed raw_json into memory at once — the same disease as Step 5 in #12. Harmless day to day (the anti-join keeps it tiny), but a Helius backfill or a `parser_version` bump would pour in a huge backlog and detonate it. A time bomb to defuse before backfill.

**Cause**: Mental model — treat the unanalyzed raw_json as a "work pile." Putting the *entire* pile on the table at once crashes it (OOM). The fix is to put one chunk of work on the table, process it, drop it, then move to the next chunk.

**Fix**: Added `fetch_unanalyzed_wallet_ids()` (cheap ID-only discovery), gave `fetch_unanalyzed_raw_swaps` a `wallet_ids` filter to scope one batch, and rewrote `main()` to process 15 wallets/batch — insert + free per batch. The "reference books" (id→address, SOL prices, token cache) load once and persist across batches; the token cache especially must persist so a symbol resolved in an early batch isn't re-fetched from Helius in a later one.

**Result**: Memory bounded by construction regardless of backlog size. Verified in the same-day recovery run (#16): analyze processed the recovered backlog in batches cleanly. Backfill prerequisite cleared.

---

## 16 · 2026-06-19 — Helius host retired + a silent failure that hid it for a day ★

**Problem**: The homepage status dot went yellow — "pipeline delayed, last classified 30h ago." Yet every pipeline run reported SUCCESS (logs green, exit 0) while no new data was landing.

**Cause** (two layers):
1. **Trigger (external)**: Helius retired the hostname collect was calling (`api-mainnet.helius-rpc.com` → 404); the correct host is `mainnet.helius-rpc.com`. Every collection request failed.
2. **The real bug (mine)**: `fetch_new_swaps` caught the failed call and returned `[]`, which the caller treated as "nothing new" and marked the wallet "up to date." A silent failure: it masked the outage for a full day and advanced `last_collected_at` on ~1,550 wallets that were never actually collected (1,530 false "ok" + 20 false "failed").

**Evidence** (reconstructed from the append-only `collected_at` column): 06-17 13:00 collected 542 wallets, 06-18 13:00 collected 44, **06-19 13:00 collected nothing** (the outage), 06-19 20:00 recovery collected 622. No refresh-window setting can produce zero collection across 1,900 wallets — only a dead API can. (This also rules out the 48h window as the cause — and that window change never even took effect, see the bonus bug below.)

**Fix**:
- Restore the host (`api-mainnet` → `mainnet`).
- Make failure impossible to mask: `fetch_new_swaps` returns `None` on a hard failure vs `[]` for genuinely nothing-new; a `None` never marks a wallet done — it stays queued and retries next run.
- **Fail loud**: if >50% of wallets error in a run, raise so the run fails (and the honest status dot goes red) instead of reporting a false SUCCESS.
- **Bonus bug found**: the "48h window" from ADR 015 never took effect — a hardcoded `max_age_hours=24` in the caller overrode the 48h default. Removed it so there's one source of truth.

**Recovery**: reset the ~1,550 polluted wallets (`last_collected_at = NULL`, status → `pending`) and re-ran; data recovered, warehouse now past 300K transactions.

**Lesson**: an external dependency breaking is inevitable; what you control is how your system *reacts*. The same Helius failure, with fail-loud code, would have alerted on day one — instead a swallowed error hid it for a day and corrupted state. Silent failures don't just hide problems, they pollute. Second lesson: the cheapest way to nail an incident's true cause is to read an append-only column and reconstruct the timeline, not to trust memory.

---

# Known issues / backlog (hit but not yet fixed, or deliberately deferred)

- **Migrate off the deprecated Enhanced Transactions API**: Helius deprecated the parsed-swap Enhanced API (still operating, but being sunset). The recommended replacement `getTransactionsForAddress` returns **raw** Solana transactions, not parsed swaps — so "migration" means rewriting the swap parser (the hardest component). The tractable path is a DEX-agnostic **balance-delta** parser (net `pre/postTokenBalances`), with the existing `parse_by_token_transfers` as the seed and current `analyzed_swaps` as ground truth. **Deliberately deferred**: the old API still works and Step 3 is now fail-loud, so a 5-minute API test (which confirmed the scope before any commitment, in the spirit of #14/#16) was enough for now. Trigger to revisit: Helius announces shutdown, or a backfill makes gTFA's time/slot filtering worth the rewrite.
- **2000-swap cap → data_clipped**: ~50% of wallets have truncated history → distorted behavior/PnL → silent misclassification. Helius upgraded to Developer (6/17); lifting the cap + backfilling history still pending (remaining prereq: Step 3 asyncio — Step 4 chunking is now done, see #15).
- **Step 3 serial collection**: ~2s/wallet, ~48 min for 1430; a deep-history backfill would hit the 2h timeout. Needs asyncio + a semaphore (the new 50 RPS allows it).
- **No proactive monitoring / alerting**: Step 3 now fails loud on a broad API outage (#16), but there's still no Cloud Monitoring alert; the OOM incident (#12) was found by hand. Proactive alerting pending.
- **No automated tests**: CI only runs lint/typecheck; pytest for the parser + PnL math still to write (should exist before adding TRANSFER ingestion, which changes the money math).

---

*Every entry is backed by evidence in the code or git history.*
