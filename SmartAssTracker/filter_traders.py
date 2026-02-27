"""
filter_traders.py - 交易者过滤器（smart_wallet_finder 流水线第二步）

功能：
  读取 collect_traders.py 输出的原始钱包数据（data/<token>_raw_traders.json），
  过滤掉做市商（交易太频繁）和小散（交易太少），
  输出候选钱包列表到 data/<token>_traders.json。
  同时把被过滤掉的钱包（附带过滤原因）保存到 data/<token>_removed_traders.json，
  方便手动排查是否误杀。

  因为只是读 JSON 做过滤，不需要调 API，所以可以快速反复调参数。

用法：
  python filter_traders.py

输入：data/pump_raw_traders.json（collect_traders.py 的输出）
输出：data/pump_traders.json
      data/pump_removed_traders.json

=== 更新日志 ===
2026-02-26: 从 collect_traders.py 拆分出来，独立运行
2026-02-26: 增加 removed_traders 输出，附带过滤原因，方便排查误杀
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding='utf-8')

# === 配置 ===
TOKEN_SYMBOL = "pump"
INPUT_DIR = "data"
OUTPUT_DIR = "data"

# === 过滤条件 ===
# 日均交易超过 50 次 → 大概率是做市商机器人，不是真人交易者（用于活跃超过 1 天的钱包）
MAX_DAILY_TRADES = 50
# 活跃时间不到 1 天的钱包，如果短时间内交易超过这个数 → 高频 bot
SHORT_WINDOW_MAX_TRADES = 20
# 总交易少于 3 次 → 偶尔路过的散户，数据太少没有参考价值
MIN_TOTAL_TRADES = 3


def load_raw_traders(token_symbol):
    """读取 collect_traders.py 输出的原始钱包数据"""
    input_path = os.path.join(INPUT_DIR, f"{token_symbol}_raw_traders.json")

    if not os.path.exists(input_path):
        print(f"找不到原始数据文件: {input_path}")
        print(f"请先运行 collect_traders.py 拉取数据")
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    traders = data.get("traders", [])
    print(f"已加载 {len(traders)} 个钱包（来自 {input_path}）")
    print(f"   代币: {data.get('token_symbol', '?')} | 扫描天数: {data.get('scan_days', '?')} | 扫描时间: {data.get('scan_time', '?')}")
    return data, traders


def filter_traders(traders):
    """过滤掉做市商（交易太频繁）和小散（交易太少）"""
    print(f"\n正在过滤 {len(traders)} 个钱包...")

    filtered = []
    removed = []  # 被踢掉的钱包，附带原因
    removed_mm = 0
    removed_low = 0

    for stats in traders:
        total_trades = stats.get("total_trades", stats["buy_count"] + stats["sell_count"])

        # 计算活跃天数
        active_seconds = max(1, stats["last_trade_ts"] - stats["first_trade_ts"])
        active_days = active_seconds / 86400

        # --- 做市商过滤（分两种情况）---
        if active_days < 1.0:
            # 活跃时间不到 1 天，直接看绝对交易次数
            daily_avg = total_trades
            if total_trades > SHORT_WINDOW_MAX_TRADES:
                removed_mm += 1
                stats["total_trades"] = total_trades
                stats["daily_avg_trades"] = round(daily_avg, 1)
                stats["active_days"] = round(active_days, 1)
                stats["remove_reason"] = f"高频做市商 (活跃<1天, 总交易 {total_trades} > {SHORT_WINDOW_MAX_TRADES})"
                removed.append(stats)
                continue
        else:
            # 活跃超过 1 天，用日均频率判断
            daily_avg = total_trades / active_days
            if daily_avg > MAX_DAILY_TRADES:
                removed_mm += 1
                stats["total_trades"] = total_trades
                stats["daily_avg_trades"] = round(daily_avg, 1)
                stats["active_days"] = round(active_days, 1)
                stats["remove_reason"] = f"高频做市商 (日均 {daily_avg:.1f} > {MAX_DAILY_TRADES})"
                removed.append(stats)
                continue

        # 低活跃过滤
        if total_trades < MIN_TOTAL_TRADES:
            removed_low += 1
            stats["total_trades"] = total_trades
            stats["daily_avg_trades"] = round(daily_avg, 1)
            stats["active_days"] = round(active_days, 1)
            stats["remove_reason"] = f"低活跃 (总交易 {total_trades} < {MIN_TOTAL_TRADES})"
            removed.append(stats)
            continue

        # 通过过滤，补充统计字段
        stats["total_trades"] = total_trades
        stats["daily_avg_trades"] = round(daily_avg, 1)
        stats["active_days"] = round(active_days, 1)
        filtered.append(stats)

    print(f"   做市商 (日均 > {MAX_DAILY_TRADES} 次): -{removed_mm}")
    print(f"   低活跃 (总计 < {MIN_TOTAL_TRADES} 次): -{removed_low}")
    print(f"   剩余候选: {len(filtered)} 个钱包")

    return filtered, removed


def save_filtered(filtered, raw_meta, token_symbol):
    """保存过滤后的候选钱包列表"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, f"{token_symbol}_traders.json")

    # 按总交易次数排序
    filtered.sort(key=lambda t: t["total_trades"], reverse=True)

    result = {
        "token_mint": raw_meta.get("token_mint", ""),
        "token_symbol": token_symbol,
        "scan_days": raw_meta.get("scan_days", 0),
        "scan_time": raw_meta.get("scan_time", ""),
        "filter_time": datetime.now(timezone.utc).isoformat(),
        "filter_settings": {
            "max_daily_trades": MAX_DAILY_TRADES,
            "short_window_max_trades": SHORT_WINDOW_MAX_TRADES,
            "min_total_trades": MIN_TOTAL_TRADES,
        },
        "total_candidates": len(filtered),
        "traders": filtered,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n已保存候选钱包 → {output_path}（共 {len(filtered)} 个）")

    # 打印全部候选钱包的表格
    print(f"\n{'='*120}")
    print(f" 全部 {len(filtered)} 个候选钱包")
    print(f"{'='*120}")
    print(f"  {'#':<4} {'钱包':<46} {'买入':>5} {'卖出':>5} {'总计':>5} {'日均':>5} {'SOL量':>10} {'稳定币量':>12} {'PUMP量':>14}")
    print(f"  {'-'*112}")

    for i, t in enumerate(filtered, 1):
        print(f"  {i:<4} {t['wallet']:<46} {t['buy_count']:>5} {t['sell_count']:>5} "
              f"{t['total_trades']:>5} {t['daily_avg_trades']:>5} "
              f"{t['total_sol_volume']:>10.1f} {t['total_stable_volume']:>12.1f} "
              f"{t.get('total_pump_amount', 0):>14.1f}")

    print(f"  {'-'*112}")


def save_removed(removed, raw_meta, token_symbol):
    """保存被过滤掉的钱包，附带原因，方便手动排查误杀"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, f"{token_symbol}_removed_traders.json")

    # 按过滤原因分组排序，同组内按总交易次数降序
    removed.sort(key=lambda t: (t["remove_reason"], -t["total_trades"]))

    result = {
        "token_mint": raw_meta.get("token_mint", ""),
        "token_symbol": token_symbol,
        "scan_time": raw_meta.get("scan_time", ""),
        "filter_time": datetime.now(timezone.utc).isoformat(),
        "filter_settings": {
            "max_daily_trades": MAX_DAILY_TRADES,
            "short_window_max_trades": SHORT_WINDOW_MAX_TRADES,
            "min_total_trades": MIN_TOTAL_TRADES,
        },
        "total_removed": len(removed),
        "traders": removed,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"已保存被过滤钱包 → {output_path}（共 {len(removed)} 个，附带过滤原因）")

    # 打印被踢钱包的表格
    print(f"\n{'='*140}")
    print(f" 全部 {len(removed)} 个被过滤钱包")
    print(f"{'='*140}")
    print(f"  {'#':<4} {'钱包':<46} {'买入':>5} {'卖出':>5} {'总计':>5} {'日均':>5} {'活跃天':>7}  {'过滤原因'}")
    print(f"  {'-'*135}")

    for i, t in enumerate(removed, 1):
        print(f"  {i:<4} {t['wallet']:<46} {t['buy_count']:>5} {t['sell_count']:>5} "
              f"{t['total_trades']:>5} {t['daily_avg_trades']:>5} {t['active_days']:>7}  {t['remove_reason']}")

    print(f"  {'-'*135}")


if __name__ == "__main__":
    print(f"{'='*80}")
    print(f" Filter Traders - {TOKEN_SYMBOL.upper()} 交易者过滤")
    print(f"{'='*80}")

    # 第一步：读取原始数据
    raw_meta, traders = load_raw_traders(TOKEN_SYMBOL)

    # 第二步：过滤做市商和小散，同时收集被过滤的钱包
    filtered, removed = filter_traders(traders)

    # 第三步：保存过滤后的候选钱包
    save_filtered(filtered, raw_meta, TOKEN_SYMBOL)

    # 第四步：保存被过滤的钱包（附带原因，方便排查误杀）
    save_removed(removed, raw_meta, TOKEN_SYMBOL)