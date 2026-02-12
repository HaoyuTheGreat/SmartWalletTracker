"""
collect_traders.py - 代币交易者收集器（smart_wallet_finder 流水线第一步）

功能：
  给定一个代币的 mint 地址，自动找到它的主池，扫描池子最近 N 天的 SWAP 交易，
  提取所有交易者钱包地址，统计每个钱包的买卖次数和交易量，初步过滤做市商和小散，
  输出候选钱包列表到 data/<token>_traders.json。

  这个候选列表会作为第二步 analyze_traders.py 的输入，对每个钱包做详细 PnL 分析。

  目前先用 PUMP 代币练手，等代码和筛选条件稳定后再扩展到其他币种。

用法：
  python collect_traders.py

输入：代码里配置的 TOKEN_MINT（目前是 PUMP）
输出：data/pump_traders.json

=== 更新日志 ===
2026-02-11: 创建文件，实现基本流程
  - 通过 DexScreener API 自动找到代币的主池（流动性最高）
  - 用 Helius Enhanced Transactions API 扫描池子的 SWAP 交易
  - 用 feePayer 识别交易者钱包（避免 ATA/代理账户问题）
  - 用 SOL 和稳定币变化方向判断买卖
  - 初步过滤：去掉做市商（日均 >50 次）和低活跃钱包（<3 次）
  - 输出候选钱包列表 + Top 10 预览
"""

import requests
import json
import time
import os
import sys
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding='utf-8')

# === 配置 ===
API_KEY = "13d0159a-4cb2-4668-a95d-faa268f0e0fb"
TOKEN_MINT = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"  # PUMP
TOKEN_SYMBOL = "pump"
SCAN_DAYS = 7  # 先扫 7 天，够用再说，不够再加到 14 或 30
OUTPUT_DIR = "data"

# 支付代币（SOL/USDC/USDT，用于判断买卖方向）
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
PAYMENT_MINTS = {WSOL_MINT, USDC_MINT, USDT_MINT}

# 过滤阈值
MAX_DAILY_TRADES = 50   # 日均交易超过 50 次 → 做市商
MIN_TOTAL_TRADES = 3    # 总交易少于 3 次 → 太少没参考价值


# ============================================================
# 第一步：找主池
# ============================================================

def find_main_pool(token_mint):
    """用 DexScreener 找到代币流动性最高的池子"""
    print(f"🔍 正在查找 {token_mint[:8]}... 的主池...")

    resp = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_mint}")
    data = resp.json()
    pairs = data.get("pairs", [])

    if not pairs:
        print("❌ 没有找到任何交易池")
        return None, None

    # 按流动性排序，取最大的
    pairs.sort(key=lambda p: (p.get("liquidity", {}).get("usd", 0) or 0), reverse=True)
    best = pairs[0]

    pool_address = best.get("pairAddress", "")
    dex = best.get("dexId", "")
    base_symbol = best.get("baseToken", {}).get("symbol", "?")
    quote_symbol = best.get("quoteToken", {}).get("symbol", "?")
    liquidity = best.get("liquidity", {}).get("usd", 0) or 0
    volume_24h = best.get("volume", {}).get("h24", 0) or 0
    txns = best.get("txns", {}).get("h24", {})
    buys_24h = txns.get("buys", 0)
    sells_24h = txns.get("sells", 0)

    print(f"   主池: {pool_address}")
    print(f"   DEX: {dex} | 交易对: {base_symbol}/{quote_symbol}")
    print(f"   流动性: ${liquidity:,.0f}")
    print(f"   24h 交易量: ${volume_24h:,.0f}")
    print(f"   24h 交易笔数: {buys_24h + sells_24h} ({buys_24h} 买 + {sells_24h} 卖)")

    # 返回池子地址和报价代币信息（用于判断是 SOL 池还是 USDC 池）
    quote_mint = best.get("quoteToken", {}).get("address", "")
    return pool_address, quote_mint


# ============================================================
# 第二步：拉取池子交易记录
# ============================================================

def fetch_pool_transactions(pool_address, days):
    """用 Helius Enhanced Transactions API 拉取池子的 SWAP 交易

    和 wallet_autopsy.py 用的是同一个 API，只是这里传的是池子地址而不是钱包地址。
    每次返回最多 100 笔，用 before 参数翻页，直到超过时间范围为止。
    """
    base_url = f"https://api.helius.xyz/v0/addresses/{pool_address}/transactions"
    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())

    all_txs = []
    last_sig = None
    page = 0

    print(f"\n📥 正在拉取池子最近 {days} 天的 SWAP 交易...")
    print(f"   截止时间: {datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    while True:
        params = {
            "api-key": API_KEY,
            "type": "SWAP",
        }
        if last_sig:
            params["before"] = last_sig

        try:
            resp = requests.get(base_url, params=params)
            data = resp.json()

            if not data or not isinstance(data, list) or len(data) == 0:
                print(f"   无更多数据，停止翻页")
                break

            # 过滤掉超过时间范围的交易
            for tx in data:
                ts = tx.get("timestamp", 0)
                if ts >= cutoff_ts:
                    all_txs.append(tx)

            page += 1
            if page % 20 == 0:
                print(f"   已拉取 {len(all_txs)} 笔交易 (第 {page} 页)...")

            # 如果这一页最后一笔已经超过时间范围，停止
            oldest_in_page = data[-1].get("timestamp", 0)
            if oldest_in_page < cutoff_ts:
                print(f"   到达 {days} 天前的时间边界，停止")
                break

            # 设置下一页起点
            last_sig = data[-1].get("signature", "")
            time.sleep(0.04)  # ~25 req/sec，低于 Helius 免费限制 30 req/sec

        except Exception as e:
            print(f"   ❌ 第 {page} 页出错: {e}")
            time.sleep(2)
            # 出错后重试一次，还不行就跳过
            continue

    print(f"   总共拉取 {len(all_txs)} 笔 SWAP 交易（{days} 天）")
    return all_txs


# ============================================================
# 第三步：从交易中提取交易者钱包
# ============================================================

def extract_traders(transactions, pool_address):
    """从池子交易记录中提取每个交易者钱包的统计信息

    交易者识别：
      - 用 feePayer（交易签名者）作为钱包地址
      - feePayer 一定是真实钱包，不会有 ATA 代理账户问题

    买卖判断：
      - SOL 减少 或 稳定币减少 → 买入（花钱买币）
      - SOL 增加 或 稳定币增加 → 卖出（卖币收钱）
      - 和 wallet_autopsy.py V3 的逻辑一致
    """
    traders = {}
    skipped_no_direction = 0

    print(f"\n🧮 正在从 {len(transactions)} 笔交易中提取交易者...")

    for tx in transactions:
        fee_payer = tx.get("feePayer", "")
        if not fee_payer or fee_payer == pool_address:
            continue

        timestamp = tx.get("timestamp", 0)

        # --- 计算 feePayer 的 SOL 变化 ---
        sol_change = 0
        for acc in tx.get("accountData", []):
            if acc.get("account") == fee_payer:
                sol_change = acc.get("nativeBalanceChange", 0) / 1e9
                break

        # --- 计算 feePayer 的稳定币变化（USDC/USDT）---
        stable_change = 0.0
        for tt in tx.get("tokenTransfers", []):
            mint = tt.get("mint", "")
            if mint in PAYMENT_MINTS and mint != WSOL_MINT:
                amount = tt.get("tokenAmount", 0)
                if tt.get("toUserAccount") == fee_payer:
                    stable_change += amount
                elif tt.get("fromUserAccount") == fee_payer:
                    stable_change -= amount

        # --- 判断买卖方向 ---
        is_buy = (sol_change < -0.005) or (stable_change < -0.01)
        is_sell = (sol_change > 0.005) or (stable_change > 0.01)

        if not is_buy and not is_sell:
            skipped_no_direction += 1
            continue

        # --- 交易金额估算 ---
        trade_sol = abs(sol_change) if abs(sol_change) > 0.005 else 0
        trade_stable = abs(stable_change) if abs(stable_change) > 0.01 else 0

        # --- 汇总到 traders 字典 ---
        if fee_payer not in traders:
            traders[fee_payer] = {
                "wallet": fee_payer,
                "buy_count": 0,
                "sell_count": 0,
                "total_sol_volume": 0.0,
                "total_stable_volume": 0.0,
                "first_trade_ts": timestamp,
                "last_trade_ts": timestamp,
            }

        t = traders[fee_payer]
        if is_buy:
            t["buy_count"] += 1
        else:
            t["sell_count"] += 1
        t["total_sol_volume"] += trade_sol
        t["total_stable_volume"] += trade_stable
        t["first_trade_ts"] = min(t["first_trade_ts"], timestamp)
        t["last_trade_ts"] = max(t["last_trade_ts"], timestamp)

    print(f"   找到 {len(traders)} 个不同的交易者钱包")
    if skipped_no_direction > 0:
        print(f"   跳过 {skipped_no_direction} 笔无法判断方向的交易")
    return traders


# ============================================================
# 第四步：过滤做市商和低活跃钱包
# ============================================================

def filter_traders(traders):
    """过滤掉做市商（交易太频繁）和小散（交易太少）"""
    print(f"\n🔧 正在过滤 {len(traders)} 个钱包...")

    filtered = {}
    removed_mm = 0
    removed_low = 0

    for wallet, stats in traders.items():
        total_trades = stats["buy_count"] + stats["sell_count"]

        # 计算活跃天数（至少算 1 天）
        active_seconds = max(1, stats["last_trade_ts"] - stats["first_trade_ts"])
        active_days = max(1.0, active_seconds / 86400)
        daily_avg = total_trades / active_days

        # 过滤做市商
        if daily_avg > MAX_DAILY_TRADES:
            removed_mm += 1
            continue

        # 过滤交易太少的
        if total_trades < MIN_TOTAL_TRADES:
            removed_low += 1
            continue

        stats["total_trades"] = total_trades
        stats["daily_avg_trades"] = round(daily_avg, 1)
        stats["active_days"] = round(active_days, 1)
        filtered[wallet] = stats

    print(f"   做市商 (日均 > {MAX_DAILY_TRADES} 次): -{removed_mm}")
    print(f"   低活跃 (总计 < {MIN_TOTAL_TRADES} 次): -{removed_low}")
    print(f"   剩余候选: {len(filtered)} 个钱包")

    return filtered


# ============================================================
# 第五步：保存结果
# ============================================================

def save_results(traders, token_symbol):
    """保存候选钱包列表到 JSON 文件"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, f"{token_symbol}_traders.json")

    # 按总交易次数降序排列
    sorted_traders = sorted(
        traders.values(),
        key=lambda t: t["total_trades"],
        reverse=True
    )

    result = {
        "token_mint": TOKEN_MINT,
        "token_symbol": token_symbol,
        "scan_days": SCAN_DAYS,
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "filter_settings": {
            "max_daily_trades": MAX_DAILY_TRADES,
            "min_total_trades": MIN_TOTAL_TRADES,
        },
        "total_candidates": len(sorted_traders),
        "traders": sorted_traders,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n💾 已保存到 {output_path}")
    print(f"   候选钱包数: {len(sorted_traders)}")

    # 打印 Top 20 预览
    print(f"\n{'='*80}")
    print(f"📊 Top 20 交易者预览")
    print(f"{'='*80}")
    print(f"  {'#':<4} {'钱包':<15} {'买入':>5} {'卖出':>5} {'总计':>5} {'日均':>5} {'SOL量':>10} {'稳定币量':>12}")
    print(f"  {'-'*72}")

    for i, t in enumerate(sorted_traders[:20], 1):
        wallet_short = t['wallet'][:12] + "..."
        print(f"  {i:<4} {wallet_short:<15} {t['buy_count']:>5} {t['sell_count']:>5} "
              f"{t['total_trades']:>5} {t['daily_avg_trades']:>5} "
              f"{t['total_sol_volume']:>10.1f} {t['total_stable_volume']:>12.1f}")

    print(f"  {'-'*72}")


# ============================================================
# 主流程
# ============================================================

if __name__ == "__main__":
    print(f"{'='*80}")
    print(f"🐋 Collect Traders - {TOKEN_SYMBOL.upper()} 代币交易者收集")
    print(f"   代币: {TOKEN_MINT}")
    print(f"   扫描天数: {SCAN_DAYS}")
    print(f"{'='*80}")

    # 第一步：找主池
    pool_address, quote_mint = find_main_pool(TOKEN_MINT)
    if not pool_address:
        print("找不到池子，退出")
        sys.exit(1)

    # 第二步：拉取池子交易
    transactions = fetch_pool_transactions(pool_address, days=SCAN_DAYS)
    if not transactions:
        print("没有拉到交易数据，退出")
        sys.exit(1)

    # 第三步：提取交易者
    traders = extract_traders(transactions, pool_address)

    # 第四步：过滤
    filtered = filter_traders(traders)

    # 第五步：保存
    save_results(filtered, token_symbol=TOKEN_SYMBOL)

    print(f"\n✅ 完成！下一步用 analyze_traders.py 对候选钱包做 PnL 分析")
