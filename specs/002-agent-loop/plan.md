# Implementation Plan: AgentLoop 规划式问答（反思回环 · 多轮 · 必收敛）

**Branch**: `002-agent-loop` | **Date**: 2026-09-04 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-agent-loop/spec.md`

## Summary

实现 docs/09 阶段 2 的 AgentLoop：把阶段 1 的固定问答流水线升级为 LangGraph 状态机
（plan → route → tool → generate → reflect → converge），检索以 `hybrid_search` 工具
身份被规划调度（兑现阶段 1 原则 I 豁免）；证据不足时反思回环、重规划只替换未执行步骤
（≤3 轮）；三重收敛保险（步数 6 / 熔断 20s / token 预算 8000）；新增会话与消息模型，
支持多轮指代消解、上下文压缩（保留证据链）、同会话串行与幂等；检查点经
AsyncPostgresSaver 落库，进程重启后续跑同一次回答。SSE 事件序扩为
plan/tool_call/evidence/answer/citations/done，done 携带收敛原因。

## Technical Context

**Language/Version**: Python 3.12（与阶段 1 同栈）

**Primary Dependencies**: 阶段 1 全部依赖 + **langgraph ≥ 1.0** +
**langgraph-checkpoint-postgres**（AsyncPostgresSaver，依赖 psycopg3，与 SQLAlchemy/
asyncpg 并存连同一库，research D2）；FastAPI SSE、SQLAlchemy 2(async)、Pydantic v2、
pytest + pytest-asyncio 不变

**Storage**: PostgreSQL 16 + pgvector（既有）；新增 session/message 表、runtime_log
扩展列；LangGraph 检查点四表由框架 `setup()` 自管（research D2）

**Testing**: pytest；全部 mock 化（章程 VII）——LLM 为脚本化 FakeLLM（plan/reflect
JSON + delta 序列 + 可编程 usage），检索为 FakeEmbedding/FakeRerank + 播种库，
检查点器连本地测试 PG（测试基座，非外部服务）；真实调用仅离线评测与人工验收

**Target Platform**: 本机 Docker Compose（api + postgres），WSL2 开发（不变）

**Project Type**: web-service（单服务；prototype/ 增量对接 plan/tool_call 事件与
session_id，仅改数据对接不改 UI）

**Performance Goals**: L1/L3 完整 Loop 端到端 P95 ≤ 30s（SC-004）；寒暄首字节
P95 < 1s（SC-005）；L0 延迟不劣于阶段 1 基线（SC-009，8s 目标不在本阶段验收）；
回环修复率 ≥ 30%（SC-002）

**Constraints**: 模型可见工具面收敛（章程 I，本阶段仅 hybrid_search，clarify Q1）；
回环 ≤3 轮、步数 ≤6、预算 8000 token、熔断 20s（FR-006/007）；同会话串行 409 +
client_msg_id 幂等（FR-012/013）；压缩保留证据链（FR-014）；测试禁真实外部服务
（章程 VII）；阶段 1 遗留 T041/T044/T045 并行推进，本阶段基线以 rerank 复测校准

**Scale/Scope**: 与阶段 1 同规模语料与评测集；新增 5 用户故事 / 18 FR / 9 SC；
会话为单租户演示规模；3 周时间盒（docs/09 阶段 2）

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 状态 | 依据 |
|---|---|---|
| I. 检索工具化 | ✅ | 本阶段兑现豁免：检索以 `hybrid_search` 工具身份进 Registry、由 plan/route 调度（FR-001/002）；可见工具面收敛，`vector_search/bm25_search` 仍为内部路径；doc_reader 仅契约（clarify Q1） |
| II. 一切皆可评测 | ✅ | 评测口径先于实现（SC-001 基线 ±2σ、SC-002 修复率 ≥30% 同配置分母，clarify Q4）；评测脚本 `--loop` 模式产出 repair_rate 与失败/修复案例（research D11） |
| III. 工具健壮性是中间件 | ✅ | Tool Protocol/Registry 统一入口：超时/异常 → 证据池失败标记由 reflect 决策（FR-004）；重试仅 LLM 客户端既有策略；本阶段无写类工具，非幂等重试问题不存在；完整执行引擎（并行/幂等中间件）留阶段 3（spec Assumptions） |
| IV. 必收敛且不编造 | ✅ | 三重保险相互独立、硬规则先行（research D9）；任何终止路径输出可用结果或拒答（FR-007/008）；convergence_reason 全程落档可审计 |
| V. 安全与合规一等公民 | ✅ | 租户身份仅来自 JWT claims（FR-017）；会话/消息/检查点 thread 均含租户边界（thread_id 含 session，session 行级过滤）；expire_at 预留 + 软删（clarify Q5）；检查点数据不入请求面 |
| VI. Contract 解耦 | ✅ | LlmClient 扩展非流式 chat + 结构化输出契约（research D4）；Tool Protocol + Pydantic 入出参（research D5）；状态机 State Schema 按 docs/03 §3.3；选型变更无（LangGraph 为章程既定栈） |
| VII. 测试纪律 | ✅ | 全测试 mock 化（FakeLLM 脚本化 plan/reflect/delta/usage）；契约测试断言新事件序与 409/幂等；检查点续跑用本地 PG 真实例（测试基座）；失败案例修复附回归用例延续阶段 1 纪律 |

**Post-design re-check**: Phase 1 设计（data-model / contracts / quickstart）完成后
复核——阶段 1 的原则 I 豁免在本设计中被兑现清除；无新增违规，Complexity Tracking 为空。

## Project Structure

### Documentation (this feature)

```text
specs/002-agent-loop/
├── plan.md              # This file
├── research.md          # Phase 0 output（D1~D12）
├── data-model.md        # Phase 1 output（session/message/runtime_log 扩展）
├── quickstart.md        # Phase 1 output（验证 1~7）
├── contracts/
│   └── api.md           # chat v2 事件契约 + sessions 接口 + 409/幂等语义
└── tasks.md             # Phase 2 output (/speckit-tasks - NOT created here)
```

### Source Code (repository root)

```text
src/
├── agent/                   # 【新增】AgentLoop 核心（docs/03 §3.8 骨架落地）
│   ├── state.py             # AgentState（add_messages / operator.add reducer）
│   ├── graph.py             # build_graph()：六节点 + 条件边 + checkpointer 注入
│   ├── nodes/
│   │   ├── plan.py          # 结构化输出规划 + route 判定 + query 改写
│   │   ├── route.py         # 纯确定性路由（无 LLM）：steps/预算/计划余量判定
│   │   ├── tool_node.py     # 按计划步调 Registry 工具，结果入 tool_results/evidence
│   │   ├── generate.py      # 证据整合生成，delta 经 stream writer 外发
│   │   ├── reflect.py       # 硬规则先行 + LLM 充分性判据（结构化输出）
│   │   └── converge.py      # final_answer/citations 组装 + convergence_reason
│   └── guards.py            # 三重保险：steps/预算记账/asyncio.timeout 熔断
├── tools/                   # 【新增】工具契约层（阶段 3 Registry 前身）
│   ├── base.py              # Tool Protocol + Registry（scope 过滤可见面）
│   ├── hybrid_search.py     # 包装 rag/hybrid.py + rag/rerank.py 纯函数（签名不变）
│   └── doc_reader.py        # 仅 Pydantic 契约与错误语义（不注册，clarify Q1）
├── services/
│   ├── answer.py            # 【改造】编排入口：快路径短路 → 会话串行/幂等 → 图执行 → SSE 转发
│   ├── sessions.py          # 【新增】会话生命周期：创建/查询/软删/串行锁/中断复位
│   ├── context_window.py    # 【新增】滑动窗口 + 阈值摘要压缩（证据链不压缩，research D7）
│   ├── clients/llm.py       # 【扩展】非流式 chat(response_format) -> LlmResult(content, usage)
│   └── runtime_log.py       # 【扩展】新增列落库（plan_rounds/steps/tokens_used/convergence_reason）
├── api/
│   ├── routes/chat.py       # 【改造】请求扩展 session_id/client_msg_id；409；事件转发
│   ├── routes/sessions.py   # 【新增】GET/DELETE /v1/sessions/{id}
│   └── schemas.py           # 【扩展】ChatRequest/事件模型/done 扩展字段
├── data/
│   ├── models.py            # 【扩展】session/message 表 + runtime_log 新列
│   └── dao.py               # 【扩展】会话/消息租户过滤方法集
└── security/jwt.py          # 不变
scripts/
├── issue_token.py           # 不变
└── run_retrieval_eval.py    # 【扩展】--loop 模式：首轮失败集 → 回环重跑 → repair_rate
prototype/index.html         # 【增量】对接 plan/tool_call 事件与 session_id（仅数据对接）
tests/
├── unit/                    # state reducer/route 判定/guards/上下文压缩/幂等键 — 全 mock
├── integration/             # 图执行路径：单轮/回环/多轮/续跑（本地 PG 检查点器）
└── contract/                # chat v2 事件序、409、幂等重放、sessions 接口
```

**Structure Decision**: 单项目结构延续阶段 1，按 docs/08 §8.4 补建 `agent/` 与
`tools/` 两目录（阶段 1 明确预留）；`services/answer.py` 从「固定流水线编排」改造为
「图执行入口」，`rag/` 检索纯函数不动、由 `tools/` 包装——阶段 3 Tool Registry
生产化时仅替换工具层实现，图与契约不变。

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

（空——设计后复核无违规：阶段 1 原则 I 豁免已由本设计兑现清除。）
