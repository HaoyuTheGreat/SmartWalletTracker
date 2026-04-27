"""
Smoke test for DuneAdapter.
Run: python scripts/test_dune_adapter.py

Verifies we can pull candidates from Dune end-to-end without touching BQ.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from lib.adapters import DuneAdapter

load_dotenv()


def main():
    adapter = DuneAdapter()
    print(f"[{adapter.source_name}] fetching candidates (query_id={adapter.source_query_id})...")
    candidates = adapter.fetch_candidates()
    print(f"[{adapter.source_name}] got {len(candidates)} candidates")

    if not candidates:
        print("WARNING: zero candidates returned")
        return

    print("\nFirst 3 candidates:")
    for c in candidates[:3]:
        print(f"  address: {c.address}")
        print(f"  chain:   {c.chain}")
        print(f"  metrics: {c.raw_metrics}")
        print()


if __name__ == "__main__":
    main()
