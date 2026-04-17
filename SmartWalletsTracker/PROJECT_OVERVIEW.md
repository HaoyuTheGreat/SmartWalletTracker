# SmartWalletsTracker - Solana Smart Wallet Tracking System

## 项目目标

在 Solana 链上找到"聪明钱包"（Smart Wallets）——那些能在价格低点买入、高点卖出的钱包，最终实现实时跟单交易。

## 项目架构

整个系统是一个 **数据管道（Pipeline）**，按顺序执行：

```
wallets_list.json (钱包列表)
        │
        ▼
┌─────────────────────────┐
│  collect_traders_swaps.py │  ← Step 1: 采集原始 SWAP 交易数据
│  (Helius REST API)        │
└───────────┬─────────────┘
            │  data/wallets_swap_data/{wallet_id}.json
            ▼
┌─────────────────────────┐
│  fetch_sol_prices.py      │  ← Step 1.5: 获取 SOL 历史价格
│  (Binance API)            │
└───────────┬─────────────┘
            │  data/sol_price_history.json
            ▼
┌─────────────────────────┐
│  analyze_wallets.py       │  ← Step 2: 解析交易，统一格式
│  (Helius DAS API)         │
└───────────┬─────────────┘
            │  data/analyzed_swaps_data/{wallet_id}.json
            ▼
┌─────────────────────────┐
│  filter_traders.py        │  ← Step 3: 分类钱包，筛选聪明钱
└───────────┬─────────────┘
            │  data/wallet_analysis.csv
            │  data/smart_wallet_candidates.json
            ▼
┌─────────────────────────┐
│  llm.py                   │  ← Step 4: Claude API 辅助分析
└─────────────────────────┘
            │  data/llm_analysis_results.json

辅助工具：
  check_swaps.py            ← 数据质量验证
```

---

## 各文件详解

### Step 1: `collect_traders_swaps.py` — 数据采集

**作用：** 从 Helius API 批量拉取每个钱包的 SWAP 交易记录。

**实现方式：**
- 从 `data/wallets_list.json` 读取钱包地址列表（目前 61 个钱包）
- 对每个钱包，调用 Helius REST API：`/v0/addresses/{address}/transactions/?type=SWAP`
- API 每次最多返回 100 条，通过 `before` 参数分页，最多拉取 2000 条
- 用 `set()` 对 transaction signature 去重，避免重复记录
- 已采集的钱包（文件已存在）自动跳过，节省 API 调用
- 零交易的钱包保存到 `data/failed_wallets/`，下次也跳过

**输出：** `data/wallets_swap_data/{address前8位}.json` — 每个钱包一个 JSON 文件，内含原始交易数据列表

---

### Step 1.5: `fetch_sol_prices.py` — SOL 历史价格

**作用：** 从 Binance 拉取 SOL 每日收盘价，用于后续计算 PnL 时把 stablecoin 换算成 SOL。

**实现方式：**
- 调用 Binance 公开 API `/api/v3/klines`，免费无需 API Key
- 每次请求 1000 天的日 K 线数据，自动分页直到拉完所有历史
- 提取每天的收盘价，以 `"YYYY-MM-DD": price` 格式存储

**输出：** `data/sol_price_history.json` — 日期到 SOL 价格的映射

---

### Step 2: `analyze_wallets.py` — 交易解析

**作用：** 把 Helius 返回的原始交易数据解析成统一的结构化格式（花了多少 SOL、买了什么 token、卖了什么 token）。

**核心挑战：** 不同交易平台（Jupiter, PUMP_AMM, ORCA, OKX, DFLOW, RAYDIUM 等）返回的数据结构不同。

**实现方式：**
- **Jupiter 交易**（`parse_jupiter`）：从 `events.swap` 中提取 `tokenInputs`/`tokenOutputs`/`nativeInput`/`nativeOutput`
- **其他所有平台**（`parse_by_token_transfers`）：通用方案，从 `tokenTransfers` 字段中匹配钱包地址，判断是花出还是收入
- **Jupiter 降级策略**：如果 Jupiter 的 `events.swap` 为空但 `tokenTransfers` 有数据，自动降级到通用解析，覆盖率达 98.5%+
- **Token Symbol 解析**（`resolve_token_symbol`）：通过 Helius DAS API (`getAsset`) 查询 mint 地址对应的 token 名称，结果缓存到 `data/token_names.json` 避免重复请求
- **SOL 价格注入**：每笔交易根据 UTC 日期匹配当天 SOL 价格，写入 `sol_price_usd` 字段
- **VERSION 机制**：文件头部有 `VERSION` 整数，修改解析逻辑后递增 VERSION，已分析的文件会自动重新解析

**输出：** `data/analyzed_swaps_data/{wallet_id}.json`，格式：
```json
{
  "version": 7,
  "swaps": [
    {
      "time": "2024-12-01 10:30:00-08:00",
      "sol_price_usd": 230.5,
      "sol_spent": 1.5,
      "sol_received": 0,
      "token_spent": [],
      "token_received": [{"mint": "xxx", "symbol": "BONK", "amount": 1000000}]
    }
  ]
}
```

---

### Step 3: `filter_traders.py` — 钱包分类与筛选

**作用：** 根据交易行为把钱包分成不同类别，筛选出"聪明钱包候选人"。

**分类标签：**

| 标签 | 判定条件 | 含义 |
|------|---------|------|
| `proxy_bot` | >50% 的交易是代理交易 | 钱包只签名，实际操作由 bot 执行 |
| `high_frequency` | 日均交易 >20 笔 | 高频交易机器人 |
| `market_maker` | 日均 >10 + 买卖比 >0.5 + 头部 token 占比 >40% | 做市商：高频、买卖对称、集中少数 token |
| `insufficient_data` | 总交易 <20 笔或活跃天数 <7 天 | 数据不足以判断 |
| `data_clipped` | 有 position 的卖出量 > 买入量 × 1.1 | 2000 条窗口没覆盖全部历史 |
| `smart_candidate` | 不含以上排除标签 + 已平仓 ≥5 + 胜率 >50% + 总 PnL >0 | **聪明钱包候选人** |

**核心算法：**

1. **代理检测**（`is_proxy_transaction`）：如果钱包地址既不在 `tokenTransfers` 的 sender/receiver 中，也没有 `events.swap` 数据，说明是 bot 代为执行

2. **做市商检测**（`is_market_maker`）：统计每个 token 的买卖次数，检查最活跃 token 的买卖是否对称（ratio 接近 1.0），以及交易是否集中在少数 token 上

3. **仓位聚合**（`aggregate_by_token`）：把所有交易按 token 分组，计算每个 token 总共花了多少 SOL 买入、卖了多少 SOL 收回。Stablecoin（USDC/USDT）按当天 SOL 价格折算成虚拟 SOL

4. **盈利计算**（`calc_performance`）：一个 position "已平仓" 当卖出量 ≥ 买入量 × 95%，PnL = SOL 收回 - SOL 投入。统计胜率和总 PnL

**输出：**
- `data/wallet_analysis.csv` — 人类可读的分析结果
- `data/smart_wallet_candidates.json` — 程序可用的完整分类数据

---

### Step 4: `llm.py` — LLM 辅助分析

**作用：** 用 Claude API（Haiku 模型）逐个分析钱包交易记录，判断是否为做市商。

**实现方式：**
- 读取 `data/analyzed_swaps_data/` 中的解析后数据
- 把每笔交易格式化成文本（时间、方向、token、金额）
- 发送给 Claude，附带做市商特征说明，让 LLM 判断
- 结果保存到 `data/llm_analysis_results.json`

**状态：** 已实现基本功能，未来可扩展为更细致的钱包画像分析。

---

### 辅助工具: `check_swaps.py` — 数据验证

**作用：** 一系列诊断函数，用来检查数据质量。

**功能：**
- `compare_counts`：对比原始交易数 vs 解析后交易数，找出遗漏
- `check_duplicate_tx`：检测重复 transaction signature
- `check_sources`：统计各交易平台的交易数量分布
- `check_empty_swaps`：找出解析后为空的记录（无 token 也无 SOL 变动）

---

## 数据目录结构

```
SmartWalletsTracker/data/
├── wallets_list.json              # 输入：61 个钱包地址
├── wallets_swap_data/             # Step 1 输出：54 个钱包的原始 SWAP 数据
│   ├── Dd1k91cW.json
│   └── ...
├── failed_wallets/                # Step 1 输出：7 个无交易/失败的钱包
├── sol_price_history.json         # Step 1.5 输出：SOL 每日价格
├── token_names.json               # Step 2 缓存：token mint → symbol 映射
├── analyzed_swaps_data/           # Step 2 输出：54 个钱包的解析后数据
│   ├── Dd1k91cW.json
│   └── ...
├── wallet_analysis.csv            # Step 3 输出：分类结果 (CSV)
├── smart_wallet_candidates.json   # Step 3 输出：分类结果 (JSON)
└── llm_analysis_results.json      # Step 4 输出：LLM 分析结果
```

---

## 技术栈

- **语言：** Python 3
- **Solana 数据源：** Helius API（REST + DAS JSON-RPC）
- **价格数据：** Binance 公开 K 线 API
- **LLM：** Anthropic Claude API (Haiku)
- **依赖：** requests, python-dotenv, pytz, anthropic

---

## 项目路线图

- [x] 数据采集（Helius API 分页拉取 SWAP 交易）
- [x] 多平台交易解析（Jupiter + 通用 tokenTransfers 降级）
- [x] SOL 历史价格注入
- [x] Token symbol 解析与缓存
- [x] 钱包分类（代理/高频/做市商/数据不足/聪明钱包）
- [x] 盈亏计算（仓位聚合、胜率、PnL）
- [x] LLM 辅助分析（基础版）
- [x] 数据质量验证工具
- [ ] ML 模型训练：用分类数据自动识别钱包类型
- [ ] 实时跟单：监控聪明钱包的新交易并推送信号
