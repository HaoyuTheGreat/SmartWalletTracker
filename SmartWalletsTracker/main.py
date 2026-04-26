"""
main.py - Pipeline orchestrator (cloud entry point)

The Cloud Run Job container runs a single command: python main.py.
This file runs the stages sequentially; any failed step makes the whole
job exit with code 1, which Cloud Scheduler treats as a failed run.

Stage order (data dependencies require sequential execution):
  1. ingest_wallets         : pull candidate wallets from Dune etc. → wallet_candidates + wallets
  2. fetch_sol_prices       : refresh SOL daily prices → sol_prices
  3. collect_traders_swaps  : fetch new swaps → raw_swaps
  4. analyze_wallets        : parse new swaps → analyzed_swaps
  5. filter_traders         : classify wallets → wallet_classifications

Failure policy:
  - ingest_wallets failure → log a warning and continue (existing wallets still get processed).
  - any other step failing → exit 1; Cloud Scheduler marks the run as failed.

Local debugging: python main.py
Cloud Run:       same — python main.py
"""

import sys
import time
import traceback

import analyze_wallets
import collect_traders_swaps
import fetch_sol_prices
import filter_traders
import ingest_wallets


def run_step(name: str, func) -> float:
    """
    Run one stage, printing its banner and elapsed time. Failures propagate so
    main() can decide the exit code. Returns elapsed seconds for the summary.
    """
    print(f"\n{'='*60}\n  {name}\n{'='*60}", flush=True)
    start = time.time()
    try:
        func()
    except Exception:
        elapsed = time.time() - start
        print(f"\n[{name}] FAILED after {elapsed:.1f}s", flush=True)
        traceback.print_exc()
        raise
    elapsed = time.time() - start
    print(f"\n[{name}] completed in {elapsed:.1f}s", flush=True)
    return elapsed


def main():
    overall_start = time.time()
    timings = {}

    # Step 1: ingest_wallets — non-fatal on failure (adapter may be down,
    # pipeline should still process the wallets we already track).
    try:
        timings["ingest_wallets"] = run_step("1/5 ingest_wallets", ingest_wallets.main)
    except Exception:
        print("[ingest_wallets] non-fatal — continuing with existing wallets", flush=True)
        timings["ingest_wallets"] = -1.0

    try:
        timings["fetch_sol_prices"] = run_step(
            "2/5 fetch_sol_prices", fetch_sol_prices.fetch_sol_prices
        )
        timings["collect_traders_swaps"] = run_step(
            "3/5 collect_traders_swaps", collect_traders_swaps.main
        )
        timings["analyze_wallets"] = run_step(
            "4/5 analyze_wallets", analyze_wallets.main
        )
        timings["filter_traders"] = run_step(
            "5/5 filter_traders", filter_traders.main
        )
    except Exception:
        # Any step raised. traceback already printed by run_step.
        total = time.time() - overall_start
        print(f"\nPipeline FAILED after {total:.1f}s total", flush=True)
        sys.exit(1)

    total = time.time() - overall_start
    print(f"\n{'='*60}")
    print(f"  Pipeline SUCCESS — total {total:.1f}s")
    for step, secs in timings.items():
        print(f"    {step:30s} {secs:6.1f}s")
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
