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
  6. collect_transfers      : fetch transfers for smart candidates → raw_transfers

Failure policy:
  - Step1: ingest_wallets failure => log a warning and continue (existing wallets still get processed).
  - If ingest_wallets.py fails, the program will continue to execute but no new wallets will be added(Soft-Fail)
  -
  - Step2: fetch_sol_prices fails hard, if I do not have new price, the program will misclassify the wallets' behavior.
  -
  - Step3: It collects wallets' swap transactions, if this stage fails, the entire program should fail,
  - because we can not collect dirty data, since it will misclassify.
  -
  - Step4: analyze_wallets.py 有两个外部依赖：
  - 1.BigQuery： 从BQ读取和往BQ写入analyzed_swaps（任何一个失败都fail）
  - 2.Helius DAS API：调取Helius API去把新mint地址解析成token symbol
  -
  - Step5: filter_traders.py
  - 纯程序运行，没有外部依赖
  -
  - Step6: collect_transfers (non-fatal) — pulls transfer history for smart
  -   candidates (for the upcoming PnL fix; not used downstream yet). Soft-fail:
  -   the core swap pipeline already succeeded, and failed wallets stay unmarked
  -   so they're retried next run (the auto-retry that swap already has).

Local debugging: python main.py
Cloud Run:       same — python main.py
"""

import sys
import time
import traceback

import analyze_wallets
import collect_traders_swaps
import collect_transfers
import fetch_sol_prices
import filter_traders
import ingest_wallets

"""
A small helper that runs one pipeline stage with logging and timing.

For each stage it receives, this function:
  1. Prints a banner so you can see which stage is running (e.g. "1/5 ingest_wallets")
  2. Starts a stopwatch
  3. Calls the stage's function via func()
  4. Prints how long the stage took (or how long before it crashed)
  5. If the stage crashed, prints the error + stack trace, then re-raises the
     exception so main() can decide whether to stop the pipeline or keep going

main() calls this 5 times, once per stage, in this order:
  1. ingest_wallets.main             — pull wallet candidates from Dune
  2. fetch_sol_prices.fetch_sol_prices — refresh SOL/USD daily prices
  3. collect_traders_swaps.main      — pull new swap transactions from Helius
  4. analyze_wallets.main            — parse swaps into a unified shape
  5. filter_traders.main             — classify wallets (smart / proxy / etc.)
"""


def run_step(name: str, func) -> float:
    # printing banner, if name is 1/5 ingest_wallets:
    # ============================================================
    # 1/5 ingest_wallets
    # ============================================================
    print(f"\n{'='*60}\n  {name}\n{'='*60}", flush=True)
    start = time.time()

    try:
        # func is a parameter that holds whatever function main() passed in.
        # Calling func() executes that function — exactly which one depends on the
        # argument passed at the call site.
        # Example:
        #   run_step("1/5 ingest_wallets", ingest_wallets.main)
        #     => name = "1/5 ingest_wallets"
        #     => func = ingest_wallets.main   (a function object, not yet executed)
        # Inside run_step:
        #   1. Prints the banner containing "1/5 ingest_wallets"
        #   2. Calls func(), which is equivalent to calling ingest_wallets.main()
        #   3. That function does the Stage 1 work (Dune ingest, write to BQ, etc.)
        func()
    except Exception:
        elapsed = time.time() - start
        print(f"\n[{name}] FAILED after {elapsed:.1f}s", flush=True)
        # prints the full traceback of the current exception to stderr.
        traceback.print_exc()
        raise
    elapsed = time.time() - start
    print(f"\n[{name}] completed in {elapsed:.1f}s", flush=True)
    return elapsed


def main():
    #current timestamp(records "right now") to record the time that the entire pipeline runs.
    overall_start = time.time()
    timings = {}

    # Step 1: ingest_wallets — non-fatal on failure (adapter may be down,
    # pipeline should still process the wallets we already track).
    try:
        timings["ingest_wallets"] = run_step("1/6 ingest_wallets", ingest_wallets.main)
    except Exception:
        print(
            "[ingest_wallets] non-fatal — continuing with existing wallets", flush=True
        )
        timings["ingest_wallets"] = -1.0

    try:
        timings["fetch_sol_prices"] = run_step(
            "2/6 fetch_sol_prices", fetch_sol_prices.fetch_sol_prices
        )
        timings["collect_traders_swaps"] = run_step(
            "3/6 collect_traders_swaps", collect_traders_swaps.main
        )
        timings["analyze_wallets"] = run_step(
            "4/6 analyze_wallets", analyze_wallets.main
        )
        timings["filter_traders"] = run_step("5/6 filter_traders", filter_traders.main)
    except Exception:
        # total time the entire pipeline took to run, now time - start time.
        total = time.time() - overall_start
        print(f"\nPipeline FAILED after {total:.1f}s total", flush=True)
        sys.exit(1)

    # Step 6: collect_transfers — non-fatal. Runs AFTER filter_traders because it
    # targets smart_candidate wallets (the tags step 5 just wrote). Soft-fail: the
    # core swap pipeline already succeeded above; any wallets that fail here stay
    # unmarked and are retried on the next daily run (same auto-retry as swap).
    try:
        timings["collect_transfers"] = run_step(
            "6/6 collect_transfers", collect_transfers.main
        )
    except Exception:
        print(
            "[collect_transfers] non-fatal — failed wallets retried next run",
            flush=True,
        )
        timings["collect_transfers"] = -1.0

    total = time.time() - overall_start
    print(f"\n{'='*60}")
    print(f"  Pipeline SUCCESS — total {total:.1f}s")
    for step, secs in timings.items():
        print(f"    {step:30s} {secs:6.1f}s")
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
