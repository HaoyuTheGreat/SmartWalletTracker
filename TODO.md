# SmartWalletsTracker — TODO

---

## 已完成 ✅

### Phase 1: MVP pipeline（本地）
- `python main.py` 本地端到端跑通
- BigQuery 作为 source of truth（5 张表 + partition/cluster）
- `requirements.txt` 固化 4 个第三方依赖

### Phase 2: 容器化 & 云部署
- `Dockerfile` + `.dockerignore`
- Image build（linux/amd64）+ push 到 Artifact Registry
- GCP Secret Manager 管 `HELIUS_API_KEY`
- 两个 Service Account 分权（runner + scheduler，least privilege）
- Cloud Run Job 部署成功（4Gi memory）
- Cloud Scheduler 每天美东早 9 点自动触发（`0 9 * * * America/New_York`）
- Makefile 一键部署（`make deploy` / `make run` / `make logs`）

### Phase 3: 文档
- `DesignDoc.txt` 重写（design decisions + rationale）
- `PROJECT_OVERVIEW.md` 已存在

---

## 当前正在做 ⏸ — Phase 4: Wallet Auto-Ingestion

**目标**：从外部数据源自动收录"聪明钱候选人"，替换手动维护 `wallets` 表。

**架构定案**：
```
Layer 1 (Discovery，噪声候选池) → Layer 2 (filter_traders, 你的 IP) → Layer 3 (ML/LLM)
```

**第一期只接 Dune Analytics**（免费档 2500 credits/月，够用）。架构上留好 adapter interface，未来加 BirdEye/Arkham 只是新 adapter 类。

**Dune 研究已完成**（见对话记录），关键事实：
- Auth: `X-DUNE-API-KEY` header，免信用卡
- Async execution: `POST /execute` → poll `/status` → `GET /results`
- 免费档可执行公开 query（5 个候选社区 query 已找到）
- Solana 数据表齐全：`solana.transactions`, `dex_solana.trades`, `dex_solana.bot_trades`（bot 预分类表！）
- 每月预算 300 credits（daily 跑一次），只用 12% 额度

**下一步（按顺序）**：
- [ ] 注册 Dune 账号 → Settings → API 创建 key
- [ ] 手动在 UI 上跑一次候选 query（推荐 [couldbebasic 的 wallet analyzer](https://dune.com/couldbebasic/wallet-analyzer-for-copy-traders)）验证输出字段
- [ ] 决定是否 fork（如果要加自定义 filter 或改参数）
- [ ] Schema migration SQL：新建 `wallet_candidates` / `wallet_sources` / `ingestion_runs` / `exchange_wallets` 表，扩展 `wallets` 加 status/filter_reason
- [ ] 写 `lib/dune_client.py`（30 行 async execution client）
- [ ] 写 `ingest_wallets.py`（Dune → candidates → filter → wallets）
- [ ] 接入 main.py 作为 Step 0
- [ ] 本地调通 → `make deploy` → 云上验证
- [ ] 更新 DesignDoc 加 ingestion 章节

**可讲述的工程点（面试 talking points）**：
- Async execution pattern with exponential backoff polling
- Adapter interface for pluggable data sources
- Idempotent ingestion (same-day re-run 不重复)
- Data provenance via `wallet_sources` table
- Safety net: `wallet_candidates` buffer + `filtered_out` status 而非 hard delete
- Observability: `ingestion_runs` metrics table
- Filter rules: known-CEX seed list + contract detection
- Rate-limit aware client

---

## Side Project（data ingestion 全部完成后再做）

### Text-to-SQL + RAG Hybrid Assistant

**构想**：一个自然语言 assistant，能回答「帮我找出五个潜在聪明钱包」这类问题。不是调现成 API，**自己写 SQL 去我的 BQ 数据库查**。

**架构**：
```
用户问题
   │
   ├── 业务定义类（"smart_candidate 怎么定义？"）
   │     → RAG 召回 DesignDoc 相关段落 → LLM 基于文档回答
   │
   └── 数据查询类（"找 5 个符合定义的聪明钱包"）
         → （RAG 先召回定义）→ Text-to-SQL 生成 SQL
         → 在 BQ 执行 → LLM 总结结果
```

**两种模式的分工**：
- **RAG** 负责"业务知识"（schema、定义、项目约定）
- **Text-to-SQL** 负责"数据查询"（精确、实时）
- **Hybrid** 模式下，RAG 召回的内容作为 Text-to-SQL prompt 的 context，让 SQL 生成更准确

**Portfolio 价值**：
- Project A (SmartWalletsTracker) = Data Engineering
- Project B (SQL Assistant) = AI Application + 复用 A 的数据
- 组合讲故事："我建了 data pipeline，然后在上面做了 LLM 自然语言查询层"

**技术栈草案**：
- LLM: Claude（`.env` 里已有 key），用 tool use / function calling
- Vector DB: 待选（pgvector / Chroma / 本地 FAISS —— 数据量小，不需要 Pinecone）
- Embedding: Voyage AI 或 OpenAI ada-002
- BQ 只读 SA（防止 LLM 写出 `DROP TABLE`）
- UI: Streamlit（可视化加分，简单）或 CLI

**三档 scope**：
- v0: 一问一答 Text-to-SQL，无 retry → 周末级
- **v1 (target): Agentic + error recovery + 多轮对话** → 2 周
- v2: + RAG context layer（真正 hybrid）→ 1 月+

**MVP 目标 = v1，然后加 RAG 进化到 v2**。

**Text-to-SQL 已知难点（面试谈资）**：
- 模棱两可的问题（"最近的钱包" —— 注册？活跃？）→ assistant 要会 ask for clarification
- JOIN 错表、聚合 level 错误 → prompt engineering + schema 充分描述
- SQL 注入风险（虽然是 LLM 生成但还是要校验）→ parameterized queries + read-only SA

---

## 📚 面试谈资（已记录在 memory）

- `analyze_wallets` 189s → 2s：predicate pushdown
- `filter_traders` 122 queries → 2 queries：batching

---

## 📖 Secrets 管理原理（已执行，保留作参考）

### 三类配置的注入方式

```
┌─ 普通配置   │ --set-env-vars       │ GCP_PROJECT, BQ_DATASET
├─ Secret    │ --set-secrets        │ HELIUS_API_KEY
└─ GCP 认证  │ 绑定 service account │ 自动（不用写）
```

### 为什么不直接把 .env 打进 image

1. 谁拿到 image = 谁拿到你所有 key
2. git 历史风险 —— 一旦进 image 进 registry，难以"撤回"
3. key 轮换困难 —— 每次换 key 都要重新 build image
4. 违反 least-privilege 原则 —— 容器应在**运行时**拿到最小权限

### 代码层透明

`os.getenv("HELIUS_API_KEY")` 一行不改。本地从 `.env` 读，云上从 Secret Manager 注入。
