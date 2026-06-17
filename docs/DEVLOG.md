# DEVLOG — SmartWalletsTracker

> 这个项目一路上遇到的问题 + 怎么解决的, 按时间顺序。从头读 = 整个开发/优化历程。
> 每条格式: **问题 (现象) → 根因 → 修复 → 结果**。新条目往底部追加。
>
> 配套文档分工:
> - **DEVLOG.md** (本文件) = 遇到了什么问题, 怎么修的 (历史/旅程)
> - **DECISIONS.md** = 为什么选 X 不选 Y (架构决策)
> - **RECAP.md** = 系统现在长什么样、怎么运转 (当前快照)

---

## 01 · 2026-02~03 — 本地 JSON 文件 → BigQuery

**问题**: 早期数据存在本地 `data/*.json`。上云后 Cloud Run 是无状态的 — 这次 run 写的文件下次 run 看不到; 而且小文件 IO 慢、没法做多维度查询 ("过去 30 天所有钱包总 volume" 算不出来)。

**根因**: 本地文件不适合无状态云环境 + 分析型 (OLAP) 工作负载。

**修复**: 写 `infra/migrate_to_bigquery.py` 一次性把本地 JSON 灌进 BigQuery; 之后所有 pipeline 表都落 BQ (强类型 schema + partition/cluster + SQL 可查)。

**结果**: 数据持久化、可并发、可聚合查询。这是整个项目从"脚本"变"系统"的起点。

---

## 02 · 2026-05 — Dune 和 Helius 的 "swap" 定义不一致

**问题**: 用 Dune query 筛出"高频交易"钱包 (trade_count 20-500), 但用 Helius 拉回来平均只有 ~31 swap/钱包, 数量差 3-5 倍。一开始以为是 bug。

**根因**: Dune `dex_solana.trades` 和 Helius `type=SWAP` 是**两家公司对同一条链的独立索引**, 对"什么算一笔 swap"定义不同 (Jupiter 2-hop 路由、Pump.fun bonding curve、LP add/remove 各家归类不一)。关键认知: **"是不是 swap"是分类器的主观判断, 不是链本身的属性。**

**修复**: 架构上保留 `raw_swaps` 存 Helius 原始 JSON + `analyzed_swaps` 做解析层 + `parser_version` 追踪版本。未来如果重定义 swap, 基于 raw 层重跑, 不用重新付费调 API。

**结果**: 多源口径不一致从"bug"变成"被架构吸收的已知差异"。面试故事 (data governance / late binding over early coupling)。

---

## 03 · 2026-05 — analyze_wallets 189s → 2s (predicate pushdown)

**问题**: `analyze_wallets.py` 每次 re-run 要决定"哪些 raw_swaps 还没解析过", 即使没有新数据也要跑 189 秒。

**根因**: 旧做法把所有 raw_swaps + analyzed_swaps 拉进 Python 在内存里做 diff — 几百 MB 网络传输 + json 解析, Python 成了瓶颈。

**修复**: 把过滤下推给 BigQuery (anti-join: `LEFT JOIN ... WHERE a.signature IS NULL`), BQ 算差集, Python 只收真正要处理的行。

**结果**: 189s → 2s (~95×)。术语: predicate pushdown。idle run 几乎瞬间退出。见 DECISIONS ADR 006。

---

## 04 · 2026-05 — filter_traders N+1: 122 → 2 queries

**问题**: `filter_traders.py` 给每个钱包算画像, 每钱包 2 个 query (raw + analyzed), 61 钱包 = 122 个 query, 每个有 ~500ms slot 启动开销 → 60+ 秒。

**根因**: N+1 query 模式 — 抽象掩盖了 IO 成本。

**修复**: 改成 2 个 `GROUP BY wallet_id` 全量 query, Python 内存里按 wallet_id 分桶。

**结果**: 60+s → 几秒。术语: N+1 elimination / batch loading。见 DECISIONS ADR 007。

---

## 05 · 2026-05 — 依赖冲突: dune-client vs requests

**问题**: `make deploy` 时 Docker build 挂: `dune-client 1.10.0 depends on requests~=2.32.5` 但我们 pin 了 `requests==2.33.1`。

**根因**: 严格 pin (`==`) 在加新依赖时容易撞版本约束。

**修复**: 降 `requests` 到 `2.32.5`, 同时满足 google-cloud-bigquery (`>=2.21,<3`) 和 dune-client (`~=2.32.5`)。

**结果**: build 通过。Lesson: pin exact 换来可复现, 代价是手动解冲突; 规模化会上 pip-tools/uv 生成 lock file。

---

## 06 · 2026-05-13 — filter_traders 撞 3600s job timeout

**问题**: Cloud Run Job 跑挂在 1 小时超时上。

**根因**: `filter_traders` 每次都把 1.5GB+ 的全部 raw_swaps 拉进 Python (即使大部分钱包数据没变)。

**修复**: 增量化 — 加 `fetch_wallets_needing_classification` (又一个 anti-join), 只处理"上次分类后有新数据"的钱包; 同时把 job task-timeout 提到 7200s。

**结果**: 从"每天重扫全部"变成"只扫变化的"。(注: 这是 OOM 那次事故的前传 — 同一个文件后来还是炸了, 见 #15。)

---

## 07 · 2026-05-18~20 — 前端一组视觉/样式 bug (合并记录)

**问题 + 修复** (Day 4-5 前端冲刺期的小坑):
- **流星像彗星不像数字串**: glyph 间距按速度比例 → 慢速时重叠。改成沿单位方向向量的固定像素间距 (22px)。
- **看不到流星 (z-stacking)**: main 的 `bg-black` 盖住了 -z-10 的 canvas。去掉 bg-black, 背景渐变层改 `fixed`。
- **Tailwind v4 语法**: `bg-gradient-*` 报错 → 改 `bg-linear-*` (v4 改了命名)。
- **tag 文字隐形**: `<html>` 没加 `dark` class → shadcn 退回 light 主题 → 深色文字在黑底上看不见。给 html 加 `dark`。

**Lesson**: 框架升级 (Tailwind v4 / Next 16 / React 19) 的 breaking change 是前端这类 bug 的常见来源 — 报错信息往往指向新语法。

---

## 08 · 2026-05-19 — win_rate 显示 4000%

**问题**: Explore 表格里 win rate 显示成 4000%、5560% 这种离谱数字。

**根因**: 数据语义不一致 — pipeline (`filter_traders.py`) 把 win_rate 存成 **0-100 的百分数** (`wins/total*100`), 但前端 `formatPercent` 又乘了一次 100, 当成 0-1 分数处理。

**修复**: 前端去掉那次 `*100`, 加注释标明 pipeline 存的是 0-100。

**结果**: 显示正常。Lesson: 跨层数据语义 (0-1 vs 0-100) 必须显式约定, 是 silent bug 的经典来源。

---

## 09 · 2026-05-22 — Vercel 前端连不上 Cloud Run 后端 (CORS)

**问题**: 前端部署到 Vercel 后, 首页正常但 Explore 表格静默加载失败 (无可见报错, 空数据)。

**根因**: 浏览器同源策略 — Cloud Run API 的 `CORS_ORIGINS` 只允许 localhost, 不含 Vercel 域名, 浏览器 block 了请求。DevTools console 里能看到 CORS error。

**修复**: 一条 gcloud 命令把 Vercel 域名加进 `CORS_ORIGINS` 环境变量; service 自动重启。

**结果**: 3 分钟解决。Lesson: 应该一开始就把 CORS_ORIGINS 设计成 per-environment 配置, 而不是 localhost 默认 + 手动加 prod。

---

## 10 · 2026-05 — React: 在 effect 里同步 setState (cascading renders)

**问题**: ESLint 报 `react-hooks/set-state-in-effect` — 在 useEffect 里同步调 setState 会触发级联重渲染。出现在 Explore 页 (loading 状态) 和 ChatPanel (从 sessionStorage 恢复对话)。

**根因**: 在 effect body 里直接 setState 不是 effect 的正确用法。

**修复**: ① Explore 页 — 改成"用 params 戳记的 result 派生 loading 状态" (不在 effect 里设 loading); ② ChatPanel — 用 `useState(() => ...)` 惰性初始化从 sessionStorage 读, 而不是 effect 里 setState。

**结果**: lint 通过 + 无级联渲染。Lesson: 懂 React 18+ 的 effect 语义 nuance, 是 frontend 面试加分点。

---

## 11 · 2026-06-10 — 首页数字是写死的, 不跟数据更新

**问题**: 首页 "5,000 candidates / 111 trading smart" 是硬编码在 JSX 里的, 真实数据早就变了 (5,018 / 165+), 网页比现实落后一个月, "Live" 绿点是假的。

**根因**: 早期为了出 demo 把数字写死了, 没接真实 API。

**修复**: 首页改成 server component, 服务端 fetch `/api/stats/dashboard` + ISR 1h 缓存; 后端 stats 接口加 `candidates_scanned` 字段; API 挂了 fallback 到静态快照 (永不白屏); 状态点诚实化 (分类停更 → 显示黄色 "pipeline delayed")。

**结果**: 所有数字实时, 每小时自动刷新。状态点变成免费的 pipeline 健康监控。

---

## 12 · 2026-06-10~11 — Pipeline OOM at 8Gi (Steps 3 + 5) ★

**问题**: 连续两天 daily run 被 signal 9 杀掉 ("memory limit reached"), 8Gi 还是爆。dashboard 数据停更。

**根因** (三层叠加):
1. **Step 3 collect** 三重 per-wallet 开销 (N+1 签名查询 + 每钱包一个 load job + 每钱包一条 UPDATE), 随钱包数从 61→1400 线性增长。
2. **Step 5 filter** (真正的大炸弹) 把所有 pending 钱包的 raw_json 一次拉进 Python — 全量 backlog 时 ~3.7GB JSON → 8-10GB Python 对象。
3. **死循环**: 分类结果最后才一次性写库 → OOM 时一行没写 → backlog 原样留给明天 → 明天面对同样大的 backlog 再死, 永不自愈。

**修复** (bounded by construction, 不是加内存):
- Step 3: 一次签名快照查询 (杀 N+1) + 5000 行缓冲 flush + 批量状态 UPDATE + 及时 `del` 大对象。
- Step 5: 25 钱包/批分块 + **每批落盘** (进度跨崩溃保留, backlog 单调收敛, 打破死循环)。

**结果**: 压力测试 1430 钱包 + 1029 分类 / 8Gi / 62 分钟通过。完整复盘见 RECAP.md Step 3。Lesson: "我以为的 bug (per-wallet 开销) 藏着更糟的 bug (无界拉取 + 死循环)" — 修 bug 前先让日志讲完整故事。

---

## 13 · 2026-06-11 — 公开的 chat 端点 = 烧钱口子

**问题**: QuerySmith chat 上线网页 = `POST /api/chat` 公开在 Cloud Run 上, 每个请求都是真金白银的 Anthropic token, 爬虫/滥用能烧爆账单。

**根因**: 6 层防御保护的是 BigQuery, 没保护 Anthropic 账单。

**修复**: 后端加内存级日预算闸门 (`CHAT_DAILY_BUDGET_USD`, 默认 $3), 超额返回 429 + 友好提示; service 设 `max-instances=1` 让计数有意义。已知局限 (内存计数、per-instance) 注释在代码里。

**结果**: 零成本验证 (预算=0 测 429) + 生产验证通过。

---

## 14 · 2026-06-17 — Helius credit 浪费: 用 dry-run 否定自己的优化假设 ★

**问题**: Helius credit 快烧穿免费额度 (779K/1M, 8 天)。诊断发现 collect 每个钱包都打昂贵的 Enhanced API (~100 credits), 而 ~65% 的钱包根本没有新 swap。

**我的假设**: 加廉价探测 (`getSignaturesForAddress` ~1 credit) 先看有没有新活动, 有才打贵接口。**我很有信心能省 ~85%。**

**关键动作**: 没有直接部署。先写只读 dry-run 脚本, 对 30 个真实钱包只跑廉价探测, 估算会跳过几个 — 不烧一分贵 credit。

**结果打脸 → pivot**: 只省 7%, 不是 85%。翻生产日志发现根因: `getSignaturesForAddress` 返回**所有类型**签名, 而去重集只有 **swap** 签名, 活跃钱包天天有非-swap 活动 → probe 区分不了"新 swap"和"新任何东西", 93% 误触发。**放弃 probe, 改成把刷新窗口 24h → 48h** (直接砍 ~2x, 跟类型问题无关)。见 DECISIONS ADR 015。

**Lesson (最重要)**: 一个零成本的 dry-run, 在部署前、在花 credit "验证"前, 就否定了我很有信心的优化。"measure before optimizing"不只是测基线, 还包括**用最便宜的方式先证伪你对优化效果的预估**。

---

# 已知问题 / 待修 (遇到了但还没修, 或主动推迟)

- **⚠️ Step 4 (analyze) 无界拉取 (定时炸弹)**: `fetch_unanalyzed_raw_swaps` 把所有待解析 raw_json 一次塞进内存 — 跟 #12 的 Step 5 同款病。日常没事 (anti-join 让它很小), 但 **Helius backfill 灌入大量数据 + bump parser_version 都会引爆它**。backfill 前必须分块 (照搬 #12 的修法)。
- **2000-swap cap → data_clipped**: ~50% 钱包历史被截断 → 行为/PnL 失真 → silent misclassification。Helius 已升 Developer 档 (6/17), 拆 cap + backfill 待做 (前置 = Step 4 分块 + Step 3 asyncio)。
- **Step 3 串行**: ~2s/钱包, 1430 个要 ~48 分钟; backfill 深挖历史会撞 2h timeout。需 asyncio + 信号量 (新的 50 RPS 可用)。
- **没有 monitoring / alerting**: 有 audit log 但无主动告警; OOM 那次是手动发现的。Cloud Monitoring alert 待加。
- **没有自动化测试**: CI 只跑 lint/typecheck; parser + PnL 计算的 pytest 待写 (加 TRANSFER 改算钱逻辑前应先有测试网)。

---

*每条都能在代码或 git 历史里找到支持证据。*
