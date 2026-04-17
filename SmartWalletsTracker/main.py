"""
main.py - Pipeline orchestrator (cloud entry point)

Cloud Run Job 容器启动后只执行一条命令：python main.py
这个文件负责依次跑 4 个阶段，任何一步失败整个 job 退出码为 1，
Cloud Scheduler 会据此判定本次调度失败。

阶段顺序 (有数据依赖，必须 sequential)：
  1. fetch_sol_prices   : 更新 SOL 历史价格 → sol_prices 表
  2. collect_traders_swaps : 拉新 swap → raw_swaps 表
  3. analyze_wallets    : 解析新 swap → analyzed_swaps 表
  4. filter_traders     : 分类钱包 → wallet_classifications 表

本地调试：python main.py
Cloud Run: 同样是 python main.py
"""

import sys
import time
import traceback

import analyze_wallets
import collect_traders_swaps
import fetch_sol_prices
import filter_traders


def run_step(name: str, func) -> float:
    """
    跑一个阶段，打印阶段 banner + 耗时。失败直接抛出，交给 main() 决定退出码。
    返回耗时秒数 (便于汇总)。
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

    try:
        timings["fetch_sol_prices"] = run_step(
            "1/4 fetch_sol_prices", fetch_sol_prices.fetch_sol_prices
        )
        timings["collect_traders_swaps"] = run_step(
            "2/4 collect_traders_swaps", collect_traders_swaps.main
        )
        timings["analyze_wallets"] = run_step(
            "3/4 analyze_wallets", analyze_wallets.main
        )
        timings["filter_traders"] = run_step(
            "4/4 filter_traders", filter_traders.main
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
