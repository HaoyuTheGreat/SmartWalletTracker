"""Smoke-test all 4 external credentials. No real workload, no cost.

Run: python scripts/smoke_test_credentials.py
Expects .env loaded with HELIUS_API_KEY, CLAUDE_API_KEY, DUNE_API_KEY,
and GOOGLE_APPLICATION_CREDENTIALS pointing to a valid SA key.
"""

import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

results = []


def check(name, fn):
    try:
        fn()
        results.append((name, "OK", ""))
    except Exception as e:
        results.append((name, "FAIL", f"{type(e).__name__}: {e}"))


def test_helius():
    key = os.environ["HELIUS_API_KEY"]
    r = requests.post(
        f"https://mainnet.helius-rpc.com/?api-key={key}",
        json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"},
        timeout=10,
    )
    r.raise_for_status()
    assert "result" in r.json(), r.text


def test_claude():
    from anthropic import Anthropic
    client = Anthropic(api_key=os.environ["CLAUDE_API_KEY"])
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        messages=[{"role": "user", "content": "ping"}],
    )
    assert resp.content


def test_dune():
    key = os.environ["DUNE_API_KEY"]
    r = requests.get(
        "https://api.dune.com/api/v1/query/1/results",
        headers={"X-Dune-API-Key": key},
        timeout=10,
    )
    assert r.status_code in (200, 404), f"unexpected {r.status_code}: {r.text[:200]}"


def test_bigquery():
    from google.cloud import bigquery
    client = bigquery.Client()
    rows = list(client.query("SELECT 1 AS ok").result())
    assert rows[0].ok == 1


if __name__ == "__main__":
    check("Helius",   test_helius)
    check("Claude",   test_claude)
    check("Dune",     test_dune)
    check("BigQuery", test_bigquery)

    print()
    for name, status, detail in results:
        marker = "[OK]  " if status == "OK" else "[FAIL]"
        print(f"{marker} {name:10s} {detail}")

    if any(s == "FAIL" for _, s, _ in results):
        sys.exit(1)
