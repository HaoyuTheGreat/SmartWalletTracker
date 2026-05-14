"""
show_scale.py — print headline metrics for the SWT BigQuery dataset.

Single-shot snapshot of project scale, useful for resume / portfolio updates.

Run from repo root:
    python scripts/show_scale.py
"""

from google.cloud import bigquery

PROJECT = "smart-wallets-tracker"
DATASET = "whale_tracker"

client = bigquery.Client(project=PROJECT)


def q(sql):
    return list(client.query(sql).result())


def n(num):
    return f"{num:,}" if num is not None else "—"


def section(title):
    print(f"\n{title}")
    print("-" * 70)


print("=" * 70)
print(f"  SWT Project Scale Snapshot — {PROJECT}.{DATASET}")
print("=" * 70)

# ---------- Ingestion funnel ----------
section("INGESTION FUNNEL  (Dune → BQ → analyzed → classified)")
row = q(f"""
SELECT
  (SELECT COUNT(*) FROM `{PROJECT}.{DATASET}.wallet_candidates`) AS candidates,
  (SELECT COUNT(*) FROM `{PROJECT}.{DATASET}.wallets`) AS tracked,
  (SELECT COUNT(DISTINCT wallet_id) FROM `{PROJECT}.{DATASET}.raw_swaps`) AS with_swaps,
  (SELECT COUNT(DISTINCT wallet_id) FROM `{PROJECT}.{DATASET}.analyzed_swaps`) AS analyzed,
  (SELECT COUNT(DISTINCT wallet_id) FROM `{PROJECT}.{DATASET}.wallet_classifications`) AS classified
""")[0]
print(f"  Dune candidates buffered ......... {n(row['candidates'])}")
print(f"  Tracked wallets (in `wallets`) ... {n(row['tracked'])}")
print(f"  Wallets with raw swap data ....... {n(row['with_swaps'])}")
print(f"  Wallets analyzed ................. {n(row['analyzed'])}")
print(f"  Wallets classified ............... {n(row['classified'])}")

# ---------- Transactions ----------
section("TRANSACTIONS  (raw_swaps)")
row = q(f"""
SELECT
  COUNT(*) AS total,
  COUNT(DISTINCT signature) AS unique_sigs,
  COUNT(DISTINCT source) AS distinct_dexes,
  ROUND(SUM(LENGTH(raw_json))/1024/1024, 1) AS raw_size_mb,
  DATE(MIN(tx_time)) AS earliest_tx,
  DATE(MAX(tx_time)) AS latest_tx
FROM `{PROJECT}.{DATASET}.raw_swaps`
""")[0]
print(f"  Total swap transactions .......... {n(row['total'])}")
print(f"  Unique tx signatures ............. {n(row['unique_sigs'])}")
print(f"  Distinct DEX sources ............. {row['distinct_dexes']}")
print(f"  Raw payload size ................. {row['raw_size_mb']} MB")
print(f"  Date range ....................... {row['earliest_tx']} → {row['latest_tx']}")

# ---------- DEX coverage ----------
section("DEX COVERAGE  (which platforms / how much)")
rows = q(f"""
SELECT source, COUNT(*) AS cnt
FROM `{PROJECT}.{DATASET}.raw_swaps`
GROUP BY source
ORDER BY cnt DESC
""")
total = sum(r['cnt'] for r in rows)
for r in rows:
    pct = r['cnt'] / total * 100
    src = r['source'] or "(null)"
    print(f"  {src:<22} {n(r['cnt']):>10}   ({pct:5.1f}%)")

# ---------- Analyzed ----------
section("ANALYZED SWAPS  (refined layer)")
row = q(f"""
SELECT
  COUNT(*) AS total,
  COUNT(DISTINCT signature) AS unique_sigs,
  MIN(parser_version) AS min_pv,
  MAX(parser_version) AS max_pv
FROM `{PROJECT}.{DATASET}.analyzed_swaps`
""")[0]
print(f"  Total analyzed rows .............. {n(row['total'])}")
print(f"  Unique signatures ................ {n(row['unique_sigs'])}")
print(f"  Parser versions present .......... v{row['min_pv']} – v{row['max_pv']}")

# ---------- Classifications ----------
section("LATEST CLASSIFICATION TAGS  (most recent snapshot per wallet)")
rows = q(f"""
WITH latest AS (
  SELECT wallet_id, tags
  FROM `{PROJECT}.{DATASET}.wallet_classifications`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY wallet_id ORDER BY classified_at DESC) = 1
)
SELECT tag, COUNT(*) AS cnt
FROM latest, UNNEST(tags) AS tag
GROUP BY tag
ORDER BY cnt DESC
""")
for r in rows:
    print(f"  {r['tag']:<25} {n(r['cnt']):>6}")

# ---------- SOL price ----------
section("SOL PRICE HISTORY")
row = q(f"""
SELECT
  COUNT(*) AS days,
  MIN(price_date) AS earliest,
  MAX(price_date) AS latest,
  ROUND(MIN(price_usd), 2) AS min_p,
  ROUND(MAX(price_usd), 2) AS max_p
FROM `{PROJECT}.{DATASET}.sol_prices`
""")[0]
print(f"  Days of history .................. {n(row['days'])}")
print(f"  Date range ....................... {row['earliest']} → {row['latest']}")
print(f"  Price range ...................... ${row['min_p']} – ${row['max_p']}")

# ---------- Storage ----------
section("STORAGE FOOTPRINT  (per-table)")
rows = q(f"""
SELECT
  table_id,
  row_count,
  ROUND(size_bytes / 1024 / 1024, 1) AS size_mb
FROM `{PROJECT}.{DATASET}.__TABLES__`
ORDER BY size_bytes DESC
""")
print(f"  {'table':<28} {'rows':>12}   {'size':>10}")
total_mb = 0
total_rows = 0
for r in rows:
    print(f"  {r['table_id']:<28} {n(r['row_count']):>12}   {r['size_mb']:>7.1f} MB")
    total_mb += r['size_mb']
    total_rows += r['row_count']
print(f"  {'TOTAL':<28} {n(total_rows):>12}   {total_mb:>7.1f} MB")

# ---------- Ingestion runs ----------
section("INGESTION RUNS  (last 7 days)")
rows = q(f"""
SELECT
  DATE(started_at) AS run_date,
  source,
  status,
  candidates_fetched,
  promoted_to_wallets
FROM `{PROJECT}.{DATASET}.ingestion_runs`
WHERE started_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
ORDER BY started_at DESC
LIMIT 10
""")
if not rows:
    print("  (no runs in the last 7 days)")
else:
    for r in rows:
        src = r['source'] or "(null)"
        print(f"  {r['run_date']}  {src:<8} {r['status']:<8} "
              f"fetched={n(r['candidates_fetched']):>5}  promoted={r['promoted_to_wallets']}")

print("\n" + "=" * 70)
