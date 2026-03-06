import requests
import pandas as pd
import time
import json as _json
from datetime import datetime, timedelta
from config import API_KEY
# === ⚙️ 配置区 ===

TARGET_WALLET = "A3vFkGBrj4MRugemEXWdSFztWgBXi9eQPtD3V28Va7WH"
START_DAY = 90
END_DAY = 0
MIN_SOL_THRESHOLD = 0.005

# === 代币地址常量 ===
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
STABLECOIN_MINTS = {USDC_MINT, USDT_MINT}
# 找目标代币时跳过这些（它们是"支付手段"，不是交易目标）
PAYMENT_MINTS = {WSOL_MINT} | STABLECOIN_MINTS


def fetch_transactions(wallet):
    """从 Helius 拉取 START_DAY ~ END_DAY 天前的所有 SWAP 交易"""
    base_url = f"https://api.helius.xyz/v0/addresses/{wallet}/transactions"
    all_txs = []
    last_signature = None
    oldest_ts = int((datetime.now() - timedelta(days=START_DAY)).timestamp())
    newest_ts = int((datetime.now() - timedelta(days=END_DAY)).timestamp())

    print(f"🕵️‍♂️ 正在扫描 {wallet[:6]}... {START_DAY} 天前 ~ {END_DAY} 天前的交易...")

    while True:
        params = {
            "api-key": API_KEY,
            "type": "SWAP",
            "limit": 100,
        }
        if last_signature:
            params["before"] = last_signature

        try:
            resp = requests.get(base_url, params=params)
            data = resp.json()

            if not data or len(data) == 0:
                break

            for tx in data:
                ts = tx.get('timestamp', 0)
                if ts < oldest_ts:
                    print(f"   已到达 {START_DAY} 天前，停止拉取")
                    return all_txs
                if ts > newest_ts:
                    continue
                all_txs.append(tx)

            last_signature = data[-1]['signature']
            print(f"   已获取 {len(all_txs)} 条交易...")
            time.sleep(0.5)

        except Exception as e:
            print(f"❌ API 出错: {e}")
            break

    return all_txs


def get_stablecoin_flow(tx, wallet):
    """计算一笔交易中目标钱包的 USDC/USDT 净流入量（正=收到, 负=花出）"""
    flow = 0.0
    for tt in tx.get('tokenTransfers', []):
        if tt.get('mint') in STABLECOIN_MINTS:
            amount = tt.get('tokenAmount', 0)
            if tt.get('toUserAccount') == wallet:
                flow += amount
            elif tt.get('fromUserAccount') == wallet:
                flow -= amount
    return flow


def fetch_current_balances(wallet):
    """用 Helius DAS API 查询钱包当前所有代币持仓"""
    url = f"https://mainnet.helius-rpc.com/?api-key={API_KEY}"
    balances = {}
    page = 1

    print("\n🔍 正在查询当前持仓...")

    while True:
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "getAssetsByOwner",
            "params": {
                "ownerAddress": wallet,
                "page": page,
                "limit": 1000,
                "displayOptions": {"showFungible": True}
            }
        }
        try:
            resp = requests.post(url, json=payload)
            data = resp.json()
            items = data.get('result', {}).get('items', [])
            if not items:
                break

            for item in items:
                iface = item.get('interface', '')
                if iface in ('FungibleToken', 'FungibleAsset'):
                    mint = item.get('id', '')
                    token_info = item.get('token_info', {})
                    raw_balance = token_info.get('balance', 0)
                    decimals = token_info.get('decimals', 0)
                    if raw_balance > 0:
                        balances[mint] = raw_balance / (10 ** decimals) if decimals > 0 else raw_balance

            if len(items) < 1000:
                break
            page += 1

        except Exception as e:
            print(f"❌ 查询持仓出错: {e}")
            break

    print(f"   找到 {len(balances)} 个代币持仓")
    return balances


def fetch_token_prices(mints):
    """用 DexScreener API 批量查询代币当前价格（USD），同时返回 SOL 价格"""
    prices = {}
    # 确保查询 SOL 价格
    all_mints = set(mints) | {WSOL_MINT}
    mint_list = list(all_mints)

    print("💲 正在查询代币价格...")

    # DexScreener 每次最多查 30 个地址
    for i in range(0, len(mint_list), 30):
        chunk = mint_list[i:i+30]
        ids = ",".join(chunk)
        try:
            resp = requests.get(f"https://api.dexscreener.com/tokens/v1/solana/{ids}")
            data = resp.json()
            for pair in data:
                mint_addr = pair.get('baseToken', {}).get('address', '')
                price_usd = pair.get('priceUsd')
                if not mint_addr or not price_usd:
                    continue
                price_val = float(price_usd)
                # 同一个代币可能有多个交易对，取流动性最高的价格
                liquidity = pair.get('liquidity', {}).get('usd', 0) or 0
                if mint_addr not in prices or liquidity > prices[mint_addr][1]:
                    prices[mint_addr] = (price_val, liquidity)
        except Exception as e:
            print(f"   ⚠️ 价格查询出错: {e}")
        time.sleep(0.3)

    # 提取价格值（去掉 liquidity 辅助信息）
    prices = {k: v[0] for k, v in prices.items()}

    sol_price = prices.get(WSOL_MINT, 0)
    print(f"   SOL 当前价格: ${sol_price:.2f}")
    print(f"   获取到 {len(prices) - 1} 个代币价格")
    return prices, sol_price


def autopsy_wallet(transactions):
    """核心分析：计算每个代币的进出账

    判断买卖逻辑（V3 - 兼容代理账户/ATA）：
      - 不再依赖 tokenTransfers 的 fromUserAccount/toUserAccount 匹配钱包
      - 改为：从交易中找到 meme 币 mint，用 SOL/稳定币 变化方向判断买卖
      - SOL/稳定币 减少 → 买入（花钱买币）
      - SOL/稳定币 增加 → 卖出（卖币收钱）
    """
    token_stats = {}

    print("\n🧮 正在进行尸检分析...")

    for tx in transactions:
        token_transfers = tx.get('tokenTransfers', [])
        if not token_transfers:
            continue

        # 找到交易中涉及的 meme 币（排除 WSOL、USDC、USDT）
        meme_mints = set()
        for tt in token_transfers:
            if tt.get('mint') not in PAYMENT_MINTS:
                meme_mints.add(tt['mint'])

        if not meme_mints:
            continue

        # 取第一个 meme 币（391/393 笔交易只有 1 个 meme 币）
        mint = list(meme_mints)[0]
        symbol = mint[:6] + "..."

        # TARGET 钱包的 SOL 变化（lamports → SOL）
        sol_change = 0
        for acc in tx.get('accountData', []):
            if acc['account'] == TARGET_WALLET:
                sol_change = acc['nativeBalanceChange'] / 1e9
                break

        # TARGET 钱包的稳定币变化（USDC/USDT）
        stable_change = get_stablecoin_flow(tx, TARGET_WALLET)

        # 过滤垃圾交易（SOL 和稳定币变化都极小）
        if abs(sol_change) < MIN_SOL_THRESHOLD and abs(stable_change) < 0.01:
            continue

        # 用资金流向判断买卖
        is_buy = (sol_change < -MIN_SOL_THRESHOLD) or (stable_change < -0.01)
        is_sell = (sol_change > MIN_SOL_THRESHOLD) or (stable_change > 0.01)

        if not is_buy and not is_sell:
            continue

        if mint not in token_stats:
            token_stats[mint] = {
                'sol_spent': 0.0, 'sol_received': 0.0,
                'stable_spent': 0.0, 'stable_received': 0.0,
                'count': 0, 'symbol': symbol
            }

        token_stats[mint]['count'] += 1

        if is_buy:
            if sol_change < -MIN_SOL_THRESHOLD:
                token_stats[mint]['sol_spent'] += abs(sol_change)
            if stable_change < -0.01:
                token_stats[mint]['stable_spent'] += abs(stable_change)
        elif is_sell:
            if sol_change > MIN_SOL_THRESHOLD:
                token_stats[mint]['sol_received'] += sol_change
            if stable_change > 0.01:
                token_stats[mint]['stable_received'] += stable_change

    return token_stats


def generate_report(token_stats, balances, prices, sol_price):
    """生成最终报告：已实现盈亏 + 未实现盈亏（持仓 × 当前价格）"""
    report = []
    total_realized_usd = 0
    total_unrealized_usd = 0
    wins = 0
    losses = 0
    holding_count = 0
    rugs = 0

    for mint, data in token_stats.items():
        # === 已实现盈亏（SOL 部分转 USD + 稳定币部分本身就是 USD）===
        sol_pnl = data['sol_received'] - data['sol_spent']
        stable_pnl = data['stable_received'] - data['stable_spent']
        realized_usd = sol_pnl * sol_price + stable_pnl

        # === 未实现盈亏（当前持仓 × 当前价格）===
        current_balance = balances.get(mint, 0)
        current_price = prices.get(mint, 0)
        unrealized_usd = current_balance * current_price

        # === 总成本 / 总收入（USD）===
        total_cost_usd = data['sol_spent'] * sol_price + data['stable_spent']
        total_revenue_usd = data['sol_received'] * sol_price + data['stable_received']

        # === 总 PnL = 已实现 + 未实现 ===
        total_pnl_usd = realized_usd + unrealized_usd
        roi = (total_pnl_usd / total_cost_usd * 100) if total_cost_usd > 0.01 else 0

        # === 持仓状态 ===
        is_holding = current_balance > 0 and unrealized_usd > 0.01

        # === 状态判断 ===
        status = "持平"
        if is_holding:
            status = "📦 持仓中"
            holding_count += 1
            if total_pnl_usd > 1:
                status = "📦 持仓(赚)"
            elif total_pnl_usd < -1:
                status = "📦 持仓(亏)"
        else:
            if total_pnl_usd > 1:
                status = "✅ 盈利"
                wins += 1
            elif total_pnl_usd < -1:
                status = "❌ 亏损"
                losses += 1

        # 老鼠仓检测
        if total_cost_usd < 1 and total_revenue_usd > 50:
            status = "⚠️ 老鼠仓?"
            rugs += 1

        total_realized_usd += realized_usd
        total_unrealized_usd += unrealized_usd

        # === 投入/套现 显示 ===
        cost_parts = []
        if data['sol_spent'] > 0.01:
            cost_parts.append(f"{data['sol_spent']:.1f} SOL")
        if data['stable_spent'] > 0.01:
            cost_parts.append(f"${data['stable_spent']:.0f}")
        cost_str = " + ".join(cost_parts) if cost_parts else "0"

        rev_parts = []
        if data['sol_received'] > 0.01:
            rev_parts.append(f"{data['sol_received']:.1f} SOL")
        if data['stable_received'] > 0.01:
            rev_parts.append(f"${data['stable_received']:.0f}")
        rev_str = " + ".join(rev_parts) if rev_parts else "0"

        hold_str = f"${unrealized_usd:,.0f}" if unrealized_usd > 0.01 else "-"

        report.append({
            "代币": data['symbol'],
            "次数": data['count'],
            "投入": cost_str,
            "套现": rev_str,
            "已实现($)": round(realized_usd, 2),
            "持仓价值($)": hold_str,
            "总PnL($)": round(total_pnl_usd, 2),
            "ROI(%)": round(roi, 1),
            "状态": status
        })

    df = pd.DataFrame(report)
    if not df.empty:
        df = df.sort_values(by="总PnL($)", ascending=False)

    return df, wins, losses, holding_count, rugs, total_realized_usd, total_unrealized_usd


if __name__ == "__main__":
    # 1. 拉取交易
    txs = fetch_transactions(TARGET_WALLET)
    if not txs:
        print("没有找到交易，退出")
        exit()

    # 2. 分析交易
    token_stats = autopsy_wallet(txs)

    # 3. 查询当前持仓
    balances = fetch_current_balances(TARGET_WALLET)

    # 4. 查询价格（交易过的代币 + 当前持仓的代币）
    all_mints = set(token_stats.keys()) | set(balances.keys())
    prices, sol_price = fetch_token_prices(all_mints)

    if sol_price == 0:
        print("❌ 无法获取 SOL 价格，退出")
        exit()

    # 5. 生成报告
    df, wins, losses, holding_count, rugs, realized, unrealized = generate_report(
        token_stats, balances, prices, sol_price
    )

    print("\n" + "=" * 70)
    print(f"💀 钱包尸检报告 V2: {TARGET_WALLET[:6]}...")
    print("=" * 70)
    print(df.to_string(index=False))
    print("-" * 70)

    closed_trades = wins + losses
    win_rate = (wins / closed_trades * 100) if closed_trades > 0 else 0

    print(f"\n📊 总结 (共 {len(txs)} 笔交易, SOL=${sol_price:.2f}):")
    print(f"💰 已实现盈亏: ${realized:+,.2f}")
    print(f"📦 未实现盈亏: ${unrealized:+,.2f} ({holding_count} 个币还在持仓)")
    print(f"💎 总盈亏: ${realized + unrealized:+,.2f}")
    print(f"🏆 胜率(已平仓): {win_rate:.1f}% ({wins} 胜 / {losses} 负)")
    print(f"🚨 疑似老鼠仓: {rugs} 个")

    if rugs > 0:
        print("\n⚠️ 警告：检测到可能的自发自买行为！")
    elif win_rate > 50 and (realized + unrealized) > 0:
        print("\n✅ 评价：这是一个靠实力赚钱的优质钱包，可以考虑跟单。")
    else:
        print("\n❌ 评价：这就是个韭菜，别跟。")

    # === 导出 ===
    wallet_short = TARGET_WALLET[:6]

    csv_file = f"report_{wallet_short}.csv"
    df.to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"\n📁 报告已导出: {csv_file}")

    json_file = f"raw_txs_{wallet_short}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        _json.dump(txs, f, ensure_ascii=False, indent=2)
    print(f"📁 原始数据已导出: {json_file} ({len(txs)} 条交易)")
