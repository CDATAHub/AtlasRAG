# Implementation Plan: 单链路 RAG 问答（入库 → 检索 → 带引用回答）

**Branch**: `001-single-chain-rag` | **Date**: 2026-09-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-single-chain-rag/spec.md`

## Summary

实现 docs/09 阶段 1 的单链路 RAG：条款文档上传后自动解析（章节层级/表格行）、父子切分、
向量 + 关键词双路索引；问答接口走混合检索（RRF 融合）→ 重排 → 带引用生成，证据不足时
拒答。以最小 HTTP 服务交付（上传 / 状态 / 问答三接口，最小 JWT 鉴权），闭卷集
Recall@5 ≥ 0.8 为验收门槛。技术选型沿用 docs/02/05 既定 ADR（pgvector + tsvector BM25、
百炼 Embedding/Rerank/LLM），不重新选型。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI（含 SSE StreamingResponse）、SQLAlchemy 2（async）+
asyncpg、pgvector（Python 扩展类型）、jieba、PyJWT、DashScope/OpenAI 兼容客户端
（Embedding/Rerank/LLM，全部依赖注入）、pytest + pytest-asyncio

**Storage**: PostgreSQL 16 + pgvector 扩展（向量 HNSW + tsvector GIN，双索引同库）

**Testing**: pytest；全部 mock 化（章程原则 VII）——LLM/Embedding/Rerank 用注入的
fake 客户端与录制夹具；真实外部调用仅限离线评测脚本与人工验收

**Target Platform**: 本机 Docker Compose（API + PostgreSQL），WSL2 开发

**Project Type**: web-service（单服务，无前端代码——prototype/ 直接对接 SSE）

**Performance Goals**: 简单事实问答端到端 P95 ≤ 8s（SC-003）；单文档入库 5 分钟内可检索
（SC-005）；闭卷集检索评测一次全量运行 ≤ 5 分钟（无生成调用）

**Constraints**: 三接口 + 最小 JWT，不做会话/多轮（FR-011）；测试路径禁真实外部服务
（FR + 章程 VII）；根目录合并文档勿手改；每次问答落本地运行档案（FR-013）

**Scale/Scope**: 102 份 kaihe 条款（约 80 万字）+ 378 份片段语料；评测集 985 + 51 条；
单租户演示；6 周内交付（docs/09 阶段 1 时间盒）

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 状态 | 依据 |
|---|---|---|
| I. 检索工具化 | ⚠️ 受限通过（见 Complexity Tracking） | 阶段 1 为路线图既定的单链路中间态；检索实现为可注册的 `hybrid_search` 工具函数（含 doc_reader），阶段 2 以 Tool 身份接入 AgentLoop，接口形态不变 |
| II. 一切皆可评测 | ✅ | 评测口径先于实现定义（闭卷集 Recall@5，命中判定见 spec Assumptions）；验收门槛 SC-001/SC-002 |
| III. 工具健壮性是中间件 | ✅ | 外部客户端统一封装：超时 + 仅可重试错误指数退避 + 依赖注入；本阶段无写类工具 |
| IV. 必收敛且不编造 | ✅ | 拒答路径（US3）+ 相似度阈值边缘走拒答；链路级超时熔断输出「资料不足」 |
| V. 安全与合规一等公民 | ✅ | 最小 JWT（clarify Q3）；三要素字段 visibility/region/expire_at 从第一版建表即预留 |
| VI. Contract 解耦 | ✅ | Embedding/Rerank/LLM 客户端以 Protocol + Pydantic 契约定义；契约即 mock 基准（原则 VII） |
| VII. 测试纪律 | ✅ | 全部测试 mock 化；契约测试覆盖三接口 SSE 事件序；失败案例修复附回归用例 |

**Post-design re-check**: 设计完成后复核——除原则 I 的既定中间态豁免外，无新增违规。

## Project Structure

### Documentation (this feature)

```text
specs/001-single-chain-rag/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── api.md           # 三接口 + SSE 事件契约
└── tasks.md             # Phase 2 output (/speckit-tasks - NOT created here)
```

### Source Code (repository root)

```text
src/
├── api/                     # 接入层（最小）
│   ├── main.py              # FastAPI 入口 + JWT 中间件挂载
│   ├── routes/
│   │   ├── documents.py     # POST /v1/documents · GET /v1/documents/{id}/status
│   │   └── chat.py          # POST /v1/chat (SSE)
│   └── schemas.py           # 请求/响应 Pydantic 模型
├── rag/                     # 单链路 RAG 核心
│   ├── parser.py            # 文本轻量结构解析（章节编号/表格行）；PDF 解析后置
│   ├── chunker.py           # 父子切分（父块=小节，子块 300~500 token，重叠 10%）
│   ├── indexer.py           # 写 pgvector + tsvector；doc_id+version 幂等
│   ├── hybrid.py            # 双路召回 + RRF 融合
│   ├── rerank.py            # 重排 + 阈值判定（拒答信号）
│   └── pipeline.py          # 入库编排：解析 → 切分 → Embedding → 索引 → 状态更新
├── services/                # 应用服务
│   ├── answer.py            # 问答编排：检索 → 引用组装 → 生成 → 拒答判定
│   ├── runtime_log.py       # 本地运行档案（FR-013）
│   └── clients/             # 外部服务客户端（全部 Protocol + 注入）
│       ├── embedding.py     # 百炼 text-embedding（1024 维）
│       ├── rerank.py        # 百炼 text-rerank
│       └── llm.py           # 百炼 chat（生成 + SSE 增量）
├── data/
│   ├── models.py            # ORM：document / chunk / runtime_log（合规三字段预留）
│   ├── dao.py               # 租户过滤强制注入
│   └── db.py                # 引擎/会话
└── security/
    └── jwt.py               # 校验中间件 + claims 解析
scripts/
├── issue_token.py           # JWT 签发脚本（本机演示用）
└── run_retrieval_eval.py    # 闭卷集检索评测（无 LLM 调用）
tests/
├── unit/                    # parser/chunker/hybrid(RRF)/rerank 阈值/jwt — 全 mock
├── integration/             # 入库→检索→问答路径（fake 客户端 + 真实 PG）
└── contract/                # 三接口契约测试（SSE 事件序、鉴权、幂等）
```

**Structure Decision**: 单项目结构，按 docs/08 §8.4 骨架裁剪到阶段 1 所需最小集
（不含 agent/、tools/、observability/、eval/ 完整目录——阶段 2/4 按需补建）；
`prototype/` 复用现有文件，仅改 SSE 端点地址。

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 原则 I（检索工具化）：阶段 1 问答入口为固定流水线，未走 AgentLoop | docs/09 路线图既定：阶段 1 先验证检索质量，阶段 2 才引入 AgentLoop | 跳过单链路直接上 AgentLoop，会把「检索质量问题」与「编排问题」混在一起，违反 06 章失败归因四象限的排查前提 |
