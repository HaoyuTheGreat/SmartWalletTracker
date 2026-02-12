import requests
import pandas as pd
import time
from datetime import datetime, timedelta

# === ⚙️ 配置区 ===
# 你的 Helius API Key
API_KEY = "13d0159a-4cb2-4668-a95d-faa268f0e0fb"

# 那个 457 SOL 大哥的地址
TARGET_WALLET = "G5nxEXuFMfV74DSnsrSatqCW32F34XUnBeq3PfDS7w5E"

# 拉取时间范围（天数）
# 例：START_DAY=90, END_DAY=60 → 只看 90 天前到 60 天前的交易
# 例：START_DAY=30, END_DAY=0  → 只看最近 30 天（默认）
START_DAY = 30
END_DAY = 0

# 过滤垃圾交易：SOL 变化量低于这个值的交易直接跳过（过滤 dust airdrop / scam 广告）
MIN_SOL_THRESHOLD = 0.005

def fetch_transactions(wallet):
    """从 Helius 拉取 START_DAY ~ END_DAY 天前的所有 SWAP 交易"""
    base_url = f"https://api.helius.xyz/v0/addresses/{wallet}/transactions"
    all_txs = []
    last_signature = None
    # 时间范围：START_DAY 天前 ~ END_DAY 天前
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
                # 跳过太新的交易（比 END_DAY 天前更新的）
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

def autopsy_wallet(transactions):
    """核心逻辑：计算每个代币的进出账

    Helius Enhanced Transactions API 返回的字段：
      - tokenTransfers: [{fromUserAccount, toUserAccount, tokenAmount, mint}, ...]
      - accountData: [{account, nativeBalanceChange, tokenBalanceChanges}, ...]
      - events: {} (空的! 不能用 events.swap)

    判断买卖逻辑：
      - 如果 TARGET_WALLET 是 tokenTransfers 的 toUserAccount → 他在买入代币（花SOL）
      - 如果 TARGET_WALLET 是 tokenTransfers 的 fromUserAccount → 他在卖出代币（收SOL）
      - SOL 变化从 accountData 的 nativeBalanceChange 获取（已包含手续费）
    """
    token_stats = {}
    # 格式: { 'mint_address': {'spent': 0, 'received': 0, 'count': 0, 'mint': '...'} }

    print("\n🧮 正在进行尸检分析...")

    for tx in transactions:
        # 1. 从 tokenTransfers 找到非 SOL 代币
        token_transfers = tx.get('tokenTransfers', [])
        if not token_transfers:
            continue

        # 找到目标代币（排除 Wrapped SOL、USDC、USDT）
        SKIP_MINTS = {
            "So11111111111111111111111111111111111111112",   # Wrapped SOL
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", # USDC
            "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
        }
        target_transfer = None
        for tt in token_transfers:
            if tt.get('mint') not in SKIP_MINTS:
                target_transfer = tt
                break

        if not target_transfer:
            continue

        mint = target_transfer['mint']
        # API 没有返回代币名，用 mint 前6位做标识
        symbol = mint[:6] + "..."

        # 2. 判断买还是卖
        is_buy = target_transfer.get('toUserAccount') == TARGET_WALLET
        is_sell = target_transfer.get('fromUserAccount') == TARGET_WALLET

        if not is_buy and not is_sell:
            continue

        # 3. 从 accountData 获取 SOL 变化量（lamports → SOL）
        sol_change = 0
        for acc in tx.get('accountData', []):
            if acc['account'] == TARGET_WALLET:
                sol_change = acc['nativeBalanceChange'] / 1e9
                break

        # 过滤垃圾交易（dust airdrop / scam 广告机器人）
        if abs(sol_change) < MIN_SOL_THRESHOLD:
            continue

        # 初始化这个币的账本
        if mint not in token_stats:
            token_stats[mint] = {'spent': 0.0, 'received': 0.0, 'count': 0, 'symbol': symbol}

        token_stats[mint]['count'] += 1

        # 记账
        if is_buy and sol_change < 0:
            # 买入：花了 SOL，拿到代币
            token_stats[mint]['spent'] += abs(sol_change)
        elif is_sell and sol_change > 0:
            # 卖出：卖掉代币，拿到 SOL
            token_stats[mint]['received'] += sol_change

    # === 生成报告 ===
    report = []
    total_spent = 0
    total_received = 0
    wins = 0
    losses = 0
    rugs = 0

    for mint, data in token_stats.items():
        net_pnl = data['received'] - data['spent']
        roi = 0
        if data['spent'] > 0:
            roi = (net_pnl / data['spent']) * 100

        # 判断胜负
        status = "持平"
        if net_pnl > 0.01:
            status = "✅ 盈利"
            wins += 1
        elif net_pnl < -0.01:
            status = "❌ 亏损"
            losses += 1

        # 🚨 老鼠仓检测：如果花费极少(接近0)，但收入很高 -> 可能是发币者
        is_sus = False
        if data['spent'] < 0.1 and data['received'] > 1.0:
            status = "⚠️ 老鼠仓?"
            is_sus = True
            rugs += 1

        total_spent += data['spent']
        total_received += data['received']

        report.append({
            "代币": data['symbol'],
            "交易次数": data['count'],
            "总投入(SOL)": round(data['spent'], 2),
            "总套现(SOL)": round(data['received'], 2),
            "净利润(SOL)": round(net_pnl, 2),
            "回报率(%)": round(roi, 2),
            "状态": status
        })

    # 转成 Pandas 表格，按利润排序
    df = pd.DataFrame(report)
    if not df.empty:
        df = df.sort_values(by="净利润(SOL)", ascending=False)
    
    return df, wins, losses, rugs, total_received - total_spent

if __name__ == "__main__":
    txs = fetch_transactions(TARGET_WALLET)
    if txs:
        df, wins, losses, rugs, total_pnl = autopsy_wallet(txs)

        print("\n" + "="*50)
        print(f"💀 钱包尸检报告: {TARGET_WALLET[:6]}...")
        print("="*50)
        print(df.to_string(index=False))
        print("-" * 50)

        total_trades = wins + losses
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

        print(f"\n📊 总结数据 (最近 {len(txs)} 笔交易):")
        print(f"💰 总净盈亏: {total_pnl:+.2f} SOL")
        print(f"🏆 胜率: {win_rate:.1f}% ({wins} 胜 / {losses} 负)")
        print(f"🚨 疑似老鼠仓/发币: {rugs} 个 (成本≈0 但获利)")

        if rugs > 0:
            print("\n⚠️ 警告：检测到该钱包可能有自发自买行为！请仔细检查标记为 '老鼠仓?' 的代币。")
        elif win_rate > 50 and total_pnl > 0:
            print("\n✅ 评价：这是一个靠实力赚钱的优质钱包，可以考虑跟单。")
        else:
            print("\n❌ 评价：这就是个韭菜，别跟。")

        # === 导出数据 ===
        import json as _json
        wallet_short = TARGET_WALLET[:6]

        # 1. 报告导出为 CSV
        csv_file = f"report_{wallet_short}.csv"
        df.to_csv(csv_file, index=False, encoding="utf-8-sig")
        print(f"\n📁 报告已导出: {csv_file}")

        # 2. 原始交易数据导出为 JSON
        json_file = f"raw_txs_{wallet_short}.json"
        with open(json_file, "w", encoding="utf-8") as f:
            _json.dump(txs, f, ensure_ascii=False, indent=2)
        print(f"📁 原始数据已导出: {json_file} ({len(txs)} 条交易)")