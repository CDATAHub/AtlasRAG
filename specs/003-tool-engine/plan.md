# Implementation Plan: Tool 层生产化（执行引擎 + MCP 接入）

**Branch**: `003-tool-engine` | **Date**: 2026-09-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/003-tool-engine/spec.md`

## Summary

在阶段 2 落地的 `Tool` Protocol / `Registry` 雏形上，把工具调用从「tool_node 直接 invoke」演进为统一执行引擎管道（权限 → 校验 → 幂等判断 → 执行 → 超时 → 重试 → 异常恢复，章程 III），兑现 doc_reader 实现，计划步扩展可选多调用并行（clarify Q1），并经 MCP 客户端把外部 Server 工具动态注册进同一注册表统一调度。检索 SQL 与既有收敛护栏不动（clarify Q3 / SC-001 检索层口径）；全部验收可在 mock/CI 层完成（章程 VII）。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: 既有 FastAPI / LangGraph / Pydantic / SQLAlchemy(asyncio) / PyJWT（ADR-009）；新增 `mcp`（Model Context Protocol Python SDK，stdio transport + in-memory 测试 transport）

**Storage**: PostgreSQL（pgvector）既有；**无新表**——执行记录为内存契约实体（`ToolExecutionRecord`，进 evidence/tool_results 与 SSE 事件），聚合治理指标沿用 `runtime_log` 既有列

**Testing**: pytest + pytest-asyncio；FakeTool（可编程故障注入）/ 播种库 / MCP in-memory transport（章程 VII：零真实外部服务）

**Target Platform**: Linux server（开发期单机 Docker Compose）

**Project Type**: web-service

**Performance Goals**: 工具调用 100% 在契约超时上限内返回（SC-002）；一步两并行调用端到端 ≤ 单调用 1.5x（SC-006）

**Constraints**: 开发与 CI 零真实外部服务调用（章程 VII）；检索 SQL 与 rerank 路径零改动（clarify Q3，SC-001 bit 级口径）；图拓扑（六节点 + 条件边）与 SSE 事件序不变，仅扩展字段

**Scale/Scope**: 2 个内置工具（hybrid_search / doc_reader）+ 1 个 MCP 来源（本地演示 Server）；单请求内并行度 ≤4（计划步多调用上限）

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 状态 | 依据 |
|---|---|---|
| I. 检索工具化 | ✅ 通过 | 工具面维持收敛 `hybrid_search`+`doc_reader`（FR-003），检索仍以工具身份经 Registry 调度；vector/bm25 内部路径不暴露 |
| II. 一切皆可评测 | ✅ 通过（口径收窄） | SC-001 取检索层 bit 级口径 + mock 全绿（clarify Q2）；端到端真实评测与悬置的 002-T045 合并执行（spec Assumptions 已声明，属阶段间债务而非豁免） |
| III. 工具健壮性是中间件 | ✅ 兑现 | 本阶段主题：七类健壮性统一进 executor（FR-004~010），禁止散落工具实现 |
| IV. 必收敛且不编造 | ✅ 通过 | 工具失败仅产生 failed 证据、交 reflect 决策（FR-010，延续 002 FR-004）；收敛三保险（max_steps/熔断/token 预算）零改动 |
| V. 安全与合规一等公民 | ✅ 通过（边界声明） | scopes 从 JWT claims 注入、执行前校验（US2，ADR-009 基础）；密级过滤按 clarify Q3 统一推迟阶段 5（docs/09 阶段 5 验收项，非本阶段豁免项） |
| VI. Contract 解耦、可演进 | ✅ 通过 | `ToolPolicy` / `ToolExecutionRecord` 以 Pydantic/dataclass 契约定义（research D1）；MCP 契约自动生成（FR-013） |
| VII. 测试纪律 | ✅ 通过 | 每个用户故事附 mock 化测试任务（FakeTool 故障注入 / MCP in-memory transport）；CI 无真实外部调用 |

**Post-design re-check**（Phase 1 设计完成后复核）：D1~D9 逐决策回看，无新增违规——
超时机制采用节点内 `asyncio.wait_for`（避开 002 的 ASGI 任务组穿透教训，research D2）；
无新表/新存储组件（D8）；检索 SQL 零改动（clarify Q3）；SSE 仅加可选字段（D8）。
Complexity Tracking 维持为空。

## Project Structure

### Documentation (this feature)

```text
specs/003-tool-engine/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   └── api.md           # SSE 事件契约扩展（v2.1）
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/
├── tools/
│   ├── base.py          # 【改造】ToolPolicy 契约（timeout/retry/idempotent/parallel_safe/
│   │                    #   required_scopes）；Registry.visible_tools 按 scopes 集合覆盖判定
│   ├── executor.py      # 【新增】执行引擎：权限→校验→幂等→执行→超时→重试→异常恢复
│   │                    #   固定管道 + ToolExecutionRecord + execute_step（并行/串行）
│   ├── hybrid_search.py # 【改造】声明 ToolPolicy；invoke 逻辑零改动
│   ├── doc_reader.py    # 【改造】兑现 invoke（租户过滤 + parent 块分章/指定章节）
│   └── mcp_client.py    # 【新增】MCP 客户端：list_tools 动态注册（mcp. 前缀）+ 健康降级
├── agent/
│   ├── state.py         # 【扩展】AgentState 增 scopes 输入字段（JWT claims 注入）
│   ├── graph.py         # 【改造】build_tool_registry 注册 doc_reader；tool_node 接 executor
│   ├── nodes/
│   │   ├── plan.py      # 【改造】工具面改由请求 scopes 驱动；PlanStep 增可选 calls 多调用
│   │   └── tool_node.py # 【改造】经 executor.execute_step 执行整步（并行/串行），
│   │                    #   tool_call 事件带 attempts/duration_ms
│   └── prompts.py       # 【改造】SYSTEM_PLANNER 多调用规划措辞（tools 占位符机制不变）
├── api/routes/chat.py   # 【改造】JWT claims.scopes 解析注入（缺省向后兼容 retrieval:read）
├── config.py            # 【扩展】工具默认策略（default_timeout_ms/max_retries）+ MCP 配置
└── services/clients/    # （不动）
scripts/
├── mcp_demo_server.py   # 【新增】本地演示 MCP Server（FastMCP，保费/等待期计算工具）
└── demo_tool_engine.py  # 【新增】零 LLM 端到端演示：executor 管道 + doc_reader + MCP（quickstart 用）
tests/
├── unit/test_tools_executor.py      # 【新增】七类中间件逐项 + 默认策略（SC-002/003/004）
├── unit/test_registry_scopes.py     # 【新增】可见面 scopes 覆盖判定（SC-005 的单测层）
├── unit/test_mcp_client.py          # 【新增】清单解析/命名空间/降级（in-memory transport）
├── integration/test_parallel_step.py# 【新增】一步多调用并行/串行/部分失败（SC-006/SC-008）
├── integration/test_doc_reader.py   # 【新增】播种库分章读取 + 跨租户 404（US3）
└── （既有 100 项测试回归：SC-001 mock 口径）
```

**Structure Decision**: 单项目结构（Option 1），沿用阶段 1/2 既有布局；全部改动收敛在 `src/tools/`（本阶段主体）、`src/agent/`（plan/tool_node/state 接线）、`src/api/routes/chat.py`（scopes 注入）与 `scripts/`（演示物）。

## Complexity Tracking

> 设计后复核（见 research.md 各决策）：无章程违规项需要正当化。新增依赖仅 `mcp`（JD 明确要求的 MCP 接入本体，不可省）；无新存储组件、无新表、无图拓扑变化。

