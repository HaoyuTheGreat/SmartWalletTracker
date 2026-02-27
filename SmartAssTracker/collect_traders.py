"""
collect_traders.py - 代币交易者收集器（smart_wallet_finder 流水线第一步）

功能：
  给定一个代币的 mint 地址，自动找到它的主池，扫描池子最近 N 天的 SWAP 交易，
  提取所有交易者钱包地址，统计每个钱包的买卖次数和交易量，
  输出未过滤的原始钱包数据到 data/<token>_raw_traders.json。

  过滤做市商和小散的逻辑已移到 filter_traders.py，可以单独运行，
  不需要每次都重新拉取链上数据。

  目前先用 PUMP 代币练手，等代码和筛选条件稳定后再扩展到其他币种。

用法：
  python collect_traders.py

输入：代码里配置的 TOKEN_MINT（目前是 PUMP）
输出：data/pump_raw_traders.json

=== 更新日志 ===
2026-02-26: 拆分过滤逻辑到 filter_traders.py，本文件只负责拉数据+提取钱包
2026-02-12: 给每一行代码加上详细中文注释
2026-02-11: 创建文件，实现基本流程
  - 通过 DexScreener API 自动找到代币的主池（流动性最高）
  - 用 Helius Enhanced Transactions API 扫描池子的 SWAP 交易
  - 用 feePayer 识别交易者钱包（避免 ATA/代理账户问题）
  - 用 SOL 和稳定币变化方向判断买卖
  - 输出候选钱包列表 + Top 10 预览
"""

import requests   # 用来发 HTTP 请求（调 API）
import json        # 用来读写 JSON 文件
import time        # 用来 sleep（控制 API 调用频率）
import os          # 用来创建文件夹、拼接文件路径
import sys         # 用来设置编码、退出程序
from datetime import datetime, timezone, timedelta  # 用来处理时间
# config.py 在上级目录 WhaleTracker/ 里，需要把上级目录加到搜索路径
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config import API_KEY
# 修复 Windows 终端中文乱码问题
sys.stdout.reconfigure(encoding='utf-8')


# 我们要扫描的代币的 mint 地址（目前是 PUMP）
TOKEN_MINT = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"  # PUMP
# 代币符号，用于命名输出文件（data/pump_traders.json）
TOKEN_SYMBOL = "pump"
# 扫描最近多少天的交易，先从 7 天开始，不够再加到 14 或 30
SCAN_DAYS = 7
# 输出文件存放目录
OUTPUT_DIR = "data"

# 支付代币的 mint 地址
# 在 Solana 上买币要花 SOL 或稳定币，这三个地址就是"花的钱"
# WSOL = Wrapped SOL（SOL 在链上交易时的代币化形式）
WSOL_MINT = "So11111111111111111111111111111111111111112"
# USDC = 美元稳定币（Circle 发行）
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
# USDT = 美元稳定币（Tether 发行）
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
# 把三个放一起，方便后面判断"这个代币是支付手段还是交易目标"
PAYMENT_MINTS = {WSOL_MINT, USDC_MINT, USDT_MINT}



# ============================================================
# 第一步：找主池
# ============================================================
# 一个代币通常有多个交易池（不同 DEX、不同交易对）
# 我们只扫流动性最高的那个池子，因为大户一般在主池交易

def find_main_pool(token_mint):
    """用 DexScreener 找到代币流动性最高的池子

    DexScreener 是一个免费的 DEX 聚合器 API，能查到所有链上的交易池信息。
    我们传入代币的 mint 地址，它返回这个代币在各个 DEX 上的所有池子。
    然后我们按流动性（liquidity）排序，选最大的那个。
    """
    print(f"正在查找 {token_mint[:8]}... 的主池...")

    # 调 DexScreener API，传入代币 mint 地址
    # 返回的 JSON 里有一个 "pairs" 列表，每个元素是一个交易池
    resp = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_mint}")
    data = resp.json()
    # 取出所有交易池列表
    pairs = data.get("pairs", [])

    # 如果一个池子都没有，说明这个代币不在任何 DEX 上交易
    if not pairs:
        print("❌ 没有找到任何交易池")
        return None, None

    # 按流动性（以 USD 计）降序排列
    # lambda p: ... 意思是对每个池子 p，取出它的 liquidity.usd 作为排序依据
    # reverse=True 表示从大到小排
    pairs.sort(key=lambda p: (p.get("liquidity", {}).get("usd", 0) or 0), reverse=True)
    # 排完序后第一个就是流动性最高的池子
    best = pairs[0]

    # 从池子信息中提取我们需要的字段
    pool_address = best.get("pairAddress", "")          # 池子的链上地址
    dex = best.get("dexId", "")                          # 在哪个 DEX（PumpSwap/Orca/Raydium）
    base_symbol = best.get("baseToken", {}).get("symbol", "?")   # 交易对左边的币（如 PUMP）
    quote_symbol = best.get("quoteToken", {}).get("symbol", "?") # 交易对右边的币（如 USDC）
    liquidity = best.get("liquidity", {}).get("usd", 0) or 0     # 池子总流动性（USD）
    volume_24h = best.get("volume", {}).get("h24", 0) or 0       # 24 小时交易量（USD）
    txns = best.get("txns", {}).get("h24", {})                    # 24 小时交易笔数
    buys_24h = txns.get("buys", 0)   # 24h 买入笔数
    sells_24h = txns.get("sells", 0) # 24h 卖出笔数

    # 打印池子信息，让我们知道选了哪个
    print(f"   主池: {pool_address}")
    print(f"   DEX: {dex} | 交易对: {base_symbol}/{quote_symbol}")
    print(f"   流动性: ${liquidity:,.0f}")
    print(f"   24h 交易量: ${volume_24h:,.0f}")
    print(f"   24h 交易笔数: {buys_24h + sells_24h} ({buys_24h} 买 + {sells_24h} 卖)")

    # 返回池子地址 和 报价代币地址（quote_mint）
    # quote_mint 后面可以用来判断这是 SOL 池还是 USDC 池
    quote_mint = best.get("quoteToken", {}).get("address", "")
    return pool_address, quote_mint


# ============================================================
# 第二步：拉取池子交易记录
# ============================================================
# 拿到池子地址后，用 Helius API 拉取这个池子上所有的 SWAP 交易
# 和 wallet_autopsy.py 用的是同一个 API，只不过那边传的是钱包地址，这里传的是池子地址

def fetch_pool_transactions(pool_address, days):
    """用 Helius Enhanced Transactions API 拉取池子的 SWAP 交易

    原理：
    - Helius 的 /v0/addresses/{地址}/transactions 接口可以查任何 Solana 地址的交易历史
    - 传入池子地址就能拿到所有经过这个池子的交易
    - 加上 type=SWAP 参数只拿 swap 类型（买卖交易），过滤掉加/撤流动性等操作
    - 每次最多返回 100 笔，需要用 "before" 参数翻页（传入上一页最后一笔的签名）
    - 从最新的交易开始往前翻，翻到超过 days 天前就停止
    """
    # 构建 API URL，把池子地址塞进去
    base_url = f"https://api.helius.xyz/v0/addresses/{pool_address}/transactions"
    # 计算时间截止点：当前时间 - days 天，转成 Unix 时间戳（秒）
    # 比如 SCAN_DAYS=7，cutoff_ts 就是 7 天前的时间戳
    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())

    all_txs = []      # 存放所有拉到的交易
    last_sig = None    # 上一页最后一笔交易的签名，用于翻页
    page = 0           # 当前页数，用于打印进度

    print(f"\n 正在拉取池子最近 {days} 天的 SWAP 交易...")
    print(f"   截止时间: {datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    # 无限循环，一页一页地拉，直到时间超出范围或没有更多数据
    while True:
        # 构建请求参数
        params = {
            "api-key": API_KEY,   # Helius API 密钥
            "type": "SWAP",       # 只要 SWAP 类型的交易（买卖）
        }
        # 如果不是第一页，告诉 API "从这个签名之前的交易开始返回"
        if last_sig:
            params["before"] = last_sig

        try:
            # 发送 GET 请求
            resp = requests.get(base_url, params=params)
            # 把 JSON 响应解析成 Python 列表（每个元素是一笔交易）
            data = resp.json()

            # 如果返回空或不是列表，说明没有更多数据了
            if not data or not isinstance(data, list) or len(data) == 0:
                print(f"   无更多数据，停止翻页")
                break

            # 遍历这一页的每笔交易
            for tx in data:
                ts = tx.get("timestamp", 0)  # 取出交易时间戳
                # 只保留截止时间之后的交易（即在我们要扫描的时间范围内）
                if ts >= cutoff_ts:
                    all_txs.append(tx)

            page += 1
            # 每 20 页打印一次进度，避免刷屏
            if page % 20 == 0:
                print(f"   已拉取 {len(all_txs)} 笔交易 (第 {page} 页)...")

            # 检查这一页最后一笔（最老的一笔）的时间
            oldest_in_page = data[-1].get("timestamp", 0)
            # 如果最老的一笔已经比截止时间还早，说明后面的交易更不可能在范围内了
            if oldest_in_page < cutoff_ts:
                print(f"   到达 {days} 天前的时间边界，停止")
                break

            # 把这一页最后一笔交易的签名记下来，作为下一页的起点
            last_sig = data[-1].get("signature", "")
            # 稍微等一下，避免触发 Helius 的速率限制（免费计划 30 req/sec）
            time.sleep(0.04)  # 0.04 秒 = ~25 req/sec，留点余量

        except Exception as e:
            # 如果某一页出错（网络问题等），打印错误信息
            print(f"    第 {page} 页出错: {e}")
            # 等 2 秒后重试
            time.sleep(2)
            continue

    print(f"   总共拉取 {len(all_txs)} 笔 SWAP 交易（{days} 天）")
    return all_txs


# ============================================================
# 第三步：从交易中提取交易者钱包
# ============================================================
# 拿到了池子的所有 SWAP 交易后，现在要从每笔交易里提取出：
# - 谁发起了这笔交易（哪个钱包）
# - 这是买入还是卖出
# - 交易了多少钱
# 然后按钱包地址汇总统计

def extract_traders(transactions, pool_address):
    """从池子交易记录中提取每个交易者钱包的统计信息

    交易者识别：
      - 用 feePayer（交易签名者/手续费支付者）作为钱包地址
      - 为什么不用 tokenTransfers 里的 fromUserAccount/toUserAccount？
        因为很多钱包通过 ATA（Associated Token Account，关联代币账户）交易，
        ATA 地址和钱包地址不一样，会导致匹配不上。
        但 feePayer 一定是发起交易的真实钱包地址。

    买卖判断：
      - 如果钱包的 SOL 减少了（花了 SOL）→ 这是一笔买入（用 SOL 买了币）
      - 如果钱包的稳定币减少了（花了 USDC）→ 这也是买入（用 USDC 买了币）
      - 如果钱包的 SOL 增加了（收到了 SOL）→ 这是一笔卖出（卖币换回了 SOL）
      - 如果钱包的稳定币增加了（收到了 USDC）→ 这也是卖出
      - 这个逻辑和 wallet_autopsy.py V3 一样
    """
    # traders 字典：key 是钱包地址，value 是该钱包的统计数据
    traders = {}
    # 记录有多少笔交易因为无法判断买卖方向而被跳过
    skipped_no_direction = 0

    print(f"\n 正在从 {len(transactions)} 笔交易中提取交易者...")

    # 遍历每一笔交易
    for tx in transactions:
        # feePayer 是这笔交易的手续费支付者，也就是发起人（真实钱包地址）
        fee_payer = tx.get("feePayer", "")
        # 如果没有 feePayer 或者 feePayer 就是池子本身，跳过
        if not fee_payer or fee_payer == pool_address:
            continue

        # 交易发生的时间戳（Unix 秒）
        timestamp = tx.get("timestamp", 0)

        # --- 第一步：看这个钱包在这笔交易中 SOL 余额变化了多少 ---
        # accountData 是一个列表，记录了这笔交易中每个相关账户的余额变化
        # 我们找到 feePayer 对应的那一条，看 nativeBalanceChange（单位是 lamports）
        # 1 SOL = 1,000,000,000 lamports（10^9），所以要除以 1e9
        sol_change = 0
        for acc in tx.get("accountData", []):
            if acc.get("account") == fee_payer:
                sol_change = acc.get("nativeBalanceChange", 0) / 1e9
                break  # 找到就不用继续遍历了

        # --- 第二步：看这个钱包的稳定币（USDC/USDT）变化了多少 ---
        # tokenTransfers 是一个列表，记录了这笔交易中所有代币的转移
        # 我们只关心 USDC 和 USDT 的转移（WSOL 已经在上面通过 SOL 变化处理了）
        stable_change = 0.0
        pump_amount = 0.0  # 这笔交易中 PUMP 代币的转移数量
        for tt in tx.get("tokenTransfers", []):
            mint = tt.get("mint", "")
            if tt.get("mint") == TOKEN_MINT:
                pump_amount += tt.get("tokenAmount", 0)
            # 如果这个转移的代币是 USDC 或 USDT（排除 WSOL，因为 SOL 变化上面已经算了）
            if mint in PAYMENT_MINTS and mint != WSOL_MINT:
                amount = tt.get("tokenAmount", 0)  # 转移的数量
                # 如果稳定币转入了 feePayer 的账户 → 钱包收到了稳定币（卖出的收入）
                if tt.get("toUserAccount") == fee_payer:
                    stable_change += amount
                # 如果稳定币从 feePayer 的账户转出 → 钱包花了稳定币（买入的支出）
                elif tt.get("fromUserAccount") == fee_payer:
                    stable_change -= amount

        # --- 第三步：根据 SOL 和稳定币的变化方向判断是买入还是卖出 ---
        # SOL 减少超过 0.005（约 $1）或 稳定币减少超过 0.01 → 买入
        # （花钱了 = 买东西了）
        is_buy = (sol_change < -0.005) or (stable_change < -0.01)
        # SOL 增加超过 0.005 或 稳定币增加超过 0.01 → 卖出
        # （收钱了 = 卖东西了）
        is_sell = (sol_change > 0.005) or (stable_change > 0.01)

        # 如果既不像买也不像卖（变化太小，可能只是手续费），跳过这笔交易
        if not is_buy and not is_sell:
            skipped_no_direction += 1
            continue

        # --- 第四步：估算这笔交易的金额 ---
        # 取 SOL 变化的绝对值作为 SOL 交易量（低于 0.005 的忽略，那只是手续费）
        trade_sol = abs(sol_change) if abs(sol_change) > 0.005 else 0
        # 取稳定币变化的绝对值作为稳定币交易量
        trade_stable = abs(stable_change) if abs(stable_change) > 0.01 else 0

        # --- 第五步：把这笔交易的信息汇总到对应钱包的统计里 ---
        # 如果这个钱包是第一次出现，初始化它的统计数据
        if fee_payer not in traders:
            traders[fee_payer] = {
                "total_pump_amount": 0.0,    # 累计获得/卖出的 PUMP 数量
                "wallet": fee_payer,           # 钱包地址
                "buy_count": 0,                # 买入次数
                "sell_count": 0,               # 卖出次数
                "total_sol_volume": 0.0,       # 累计 SOL 交易量
                "total_stable_volume": 0.0,    # 累计稳定币交易量
                "first_trade_ts": timestamp,   # 最早一笔交易的时间
                "last_trade_ts": timestamp,    # 最晚一笔交易的时间
            }

        # 取出该钱包的统计对象（引用，修改 t 就是修改 traders[fee_payer]）
        t = traders[fee_payer]
        # 根据买卖方向累加计数
        if is_buy:
            t["buy_count"] += 1
        else:
            t["sell_count"] += 1
        # 累加交易量
        t["total_sol_volume"] += trade_sol
        t["total_stable_volume"] += trade_stable
        t["total_pump_amount"] += pump_amount
        # 更新最早/最晚交易时间
        t["first_trade_ts"] = min(t["first_trade_ts"], timestamp)
        t["last_trade_ts"] = max(t["last_trade_ts"], timestamp)

    print(f"   找到 {len(traders)} 个不同的交易者钱包")
    if skipped_no_direction > 0:
        print(f"   跳过 {skipped_no_direction} 笔无法判断方向的交易")
    return traders


# ============================================================
# 第四步：保存原始钱包数据
# ============================================================
# 把提取出的所有交易者钱包数据保存到 JSON 文件（未过滤）
# 过滤逻辑已移到 filter_traders.py

def save_raw_traders(traders, token_symbol):
    """保存未过滤的原始钱包数据到 JSON 文件"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, f"{token_symbol}_raw_traders.json")

    # 给每个钱包补充 total_trades 字段，按总交易次数排序
    traders_list = []
    for wallet, stats in traders.items():
        stats["total_trades"] = stats["buy_count"] + stats["sell_count"]
        traders_list.append(stats)

    traders_list.sort(key=lambda t: t["total_trades"], reverse=True)

    result = {
        "token_mint": TOKEN_MINT,
        "token_symbol": token_symbol,
        "scan_days": SCAN_DAYS,
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "total_wallets": len(traders_list),
        "traders": traders_list,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n 已保存原始数据到 {output_path}")
    print(f"   总钱包数: {len(traders_list)}")
    print(f"   下一步运行 filter_traders.py 进行过滤")


# ============================================================
# 主流程
# ============================================================
# 这里是程序的入口。if __name__ == "__main__" 意思是：
# 只有直接运行这个文件时才执行下面的代码
# 如果是被其他文件 import 的话就不执行

if __name__ == "__main__":
    # 打印标题
    print(f"{'='*80}")
    print(f" Collect Traders - {TOKEN_SYMBOL.upper()} 代币交易者收集")
    print(f"   代币: {TOKEN_MINT}")
    print(f"   扫描天数: {SCAN_DAYS}")
    print(f"{'='*80}")

    # 第一步：调 DexScreener 找到 PUMP 代币流动性最高的池子
    pool_address, quote_mint = find_main_pool(TOKEN_MINT)
    if not pool_address:
        print("找不到池子，退出")
        sys.exit(1)  # 退出程序，返回错误码 1

    # 第二步：用 Helius API 拉取这个池子最近 N 天的所有 SWAP 交易
    transactions = fetch_pool_transactions(pool_address, days=SCAN_DAYS)
    if not transactions:
        print("没有拉到交易数据，退出")
        sys.exit(1)

    # 第三步：从每笔交易中提取交易者钱包地址，统计每个钱包的买卖次数和金额
    traders = extract_traders(transactions, pool_address)

    # 第四步：保存未过滤的原始钱包数据到 JSON
    save_raw_traders(traders, token_symbol=TOKEN_SYMBOL)
