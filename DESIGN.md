# WhaleTracker - Design Doc

## 最终目标

**自动发现并筛选 Solana 上交易主流币的"聪明钱包"，生成可跟单的排行榜。**

不是找 meme coin 赌狗，而是找那些在 JUP、JTO、PYTH、WIF、BONK 等主流 Solana 代币上持续盈利的钱包。

最终产出：一个每天/每周更新的 **Top 20 聪明钱包排行榜**，包含每个钱包的胜率、PnL、交易风格等指标，供跟单参考。

---

## 整体架构

```
┌─────────────────────────────────────────────────────┐
│                 smart_wallet_finder.py               │
│                 (核心流水线 - 待开发)                   │
├─────────────────────────────────────────────────────┤
│                                                     │
│  第一步：收集候选钱包                                  │
│    输入一个主流币 mint → 找到它的 DEX 池子              │
│    → 扫描池子最近 30 天交易 → 提取所有交易者钱包          │
│                                                     │
│  第二步：过滤垃圾                                      │
│    去掉做市商（交易频率 > 50次/天）                      │
│    去掉交易所钱包（交易对手高度集中）                     │
│    去掉只交易一两次的钱包                               │
│                                                     │
│  第三步：批量分析                                      │
│    对每个候选钱包跑 wallet_autopsy                      │
│    计算 PnL、胜率、ROI                                │
│                                                     │
│  第四步：输出排行榜                                    │
│    按 PnL + 胜率综合排名                               │
│    输出 Top 20 聪明钱包                                │
│                                                     │
└─────────────────────────────────────────────────────┘
```

---

## 当前已有的工具

| 文件 | 功能 | 状态 |
|------|------|------|
| `real_whale_watcher.py` | 实时监控 USDC/USDT 大额转账，发布到 Pub/Sub | 已完成 |
| `wallet_autopsy.py` | 分析单个钱包的交易历史，计算 PnL/胜率 | 已完成 (V3) |
| `fetch_holdings.py` | 查询钱包当前持仓和价值 | 已完成 |
| `smart_wallet_finder.py` | 自动发现聪明钱包的核心流水线 | **待开发** |

---

## 开发原则

**先用笨办法跑通，再逐步优化。**

早期阶段全部用本地文件（JSON/CSV）存储中间数据，不引入数据库或云服务。
每一步的输出都存成文件，下一步读文件作为输入。这样：
- 调试方便：中间结果可以随时检查
- 不浪费 API 调用：拉过的数据不用重新拉
- 筛选条件可以反复调：改过滤逻辑不需要重跑上游

等筛选条件和分析逻辑都稳定之后，再接入 GCP（BigQuery 存储、Pub/Sub 实时推送、Cloud Functions 定时运行）。

---

## 现在的第一步

### 目标：写 `smart_wallet_finder.py` 的第一个版本

**输入**: 一个主流币的 mint 地址（比如 JUP）
**输出**: 这个币的 Top 20 盈利钱包排行榜

### 拆成三个脚本，分步执行

```
步骤 1: collect_traders.py
  输入: 代币 mint 地址
  做什么: 找池子 → 扫交易 → 提取钱包 → 初步过滤做市商
  输出: data/jup_traders.json (候选钱包列表 + 每个钱包的交易次数)

步骤 2: analyze_traders.py
  输入: data/jup_traders.json
  做什么: 对每个候选钱包跑 autopsy 分析
  输出: data/jup_analysis.json (每个钱包的 PnL、胜率、ROI)

步骤 3: rank_traders.py
  输入: data/jup_analysis.json
  做什么: 排序 + 过滤 + 生成排行榜
  输出: data/jup_leaderboard.csv (Top 20 聪明钱包)
```

这样拆分的好处：
- 步骤 1 最慢（大量 API 调用），跑一次就存下来，后续调参不用重跑
- 步骤 2 可以断点续跑（已分析的钱包跳过）
- 步骤 3 纯本地计算，秒出结果，方便反复调筛选条件

### 技术依赖

- **Helius API** — 交易数据（已有 key）
- **DexScreener API** — 池子发现 + 价格查询（免费，无需 key）
- **Helius DAS API** — 持仓查询（已有 key）

### 需要注意的坑（从之前的经验中学到的）

- 钱包可能通过 ATA/代理账户交易，不能只匹配 `fromUserAccount`/`toUserAccount` → 用 SOL 变化方向判断买卖
- Jupiter 聚合路由会产生多跳交易，一笔交易可能有多个 tokenTransfers → 取非支付代币的 mint
- DexScreener 价格覆盖不全，Helius DAS `price_info` 可以补充 → 双层价格策略
- Helius API 有速率限制 → 批量分析时需要控制并发和加 sleep

---

## 后续阶段

### Phase 2: 完善筛选 + 多币种（本地）

- 跑多个主流币（JUP、JTO、PYTH、WIF、BONK 等），合并排行榜
- 优化做市商检测（volume/PnL 比、交易时间分布等）
- 加入更多指标：最大回撤、平均持仓时间、交易频率

### Phase 3: 上云（GCP）

- 数据存入 BigQuery，替代本地 JSON/CSV
- Cloud Functions 定时跑流水线（每天/每周）
- 接入 real_whale_watcher：聪明钱包有新交易时通过 Pub/Sub 推送告警

### Phase 4: 产品化（远期）

- Web 面板展示排行榜
- 自动跟单（需要非常谨慎）
- 多链扩展（ETH、Base 等）
