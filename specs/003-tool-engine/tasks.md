---
description: "Task list for feature implementation"
---

# Tasks: Tool 层生产化（执行引擎 + MCP 接入）

**Input**: Design documents from `/specs/003-tool-engine/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Constitution Principle VII (Test Discipline) requires test tasks for every feature — include mock-based unit/integration tests (no real external services: LLM/Embedding/Rerank/Storage); only offline evaluation and manual acceptance may call real services.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: `src/`, `tests/` at repository root
- Paths shown below assume single project - adjust based on plan.md structure

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 依赖与配置就位

- [x] T001 [P] `pyproject.toml` 新增依赖 `mcp>=1.0`（MCP Python SDK）并 `uv sync` 验证导入（research D7）
- [x] T002 [P] `src/config.py` Settings 扩展：`tool_default_timeout_ms=5000`、`tool_default_max_retries=1`、`retry_backoff_base_s=0.2`、`mcp_server_cmd`（演示 Server 启动命令）、`mcp_tool_scopes=["misc:calc"]`（research D1/D7）

**Checkpoint**: 依赖与默认策略可配置

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 工具契约层演进——全部 US 的公共前提

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T003 `src/tools/base.py` 演进（research D1/D3）：新增 `ToolPolicy` dataclass（timeout_ms/max_retries/idempotent/parallel_safe/required_scopes，见 data-model.md 表）；`Tool` Protocol 增可选 `policy` 属性（缺省由 Registry/Executor 按 Settings 保守默认解析，单值 `scope` 作 required_scopes sugar）；`ToolContext` 增 `scopes: list[str]`；`Registry.visible_tools(scopes)` 改为「required_scopes ⊆ 请求 scopes」覆盖判定（FR-003）；新增 `ToolTimeoutError` / `ToolTransientError`（ToolError 子类）
- [x] T004 `src/tools/hybrid_search.py` 声明 `policy`：required_scopes=("retrieval:read",)、timeout_ms=8000、max_retries=1、idempotent=True、parallel_safe=True（data-model.md 内置工具表）；invoke 逻辑零改动（SC-001 透传红线）

**Checkpoint**: 契约层就绪，既有 100 项测试仍全绿（visible_tools 对 ["retrieval:read"] 行为不变）

## Phase 3: User Story 1 - 执行引擎核心管道 (Priority: P1) 🎯 MVP

**Goal**: 任何一次工具调用经统一管道获得校验/超时/重试/异常恢复保障（FR-004~010）

**Independent Test**: `uv run pytest tests/unit/test_tools_executor.py -q` 全绿（SC-002/003/004/008 的单测层断言）

### Tests for User Story 1 (REQUIRED by Constitution VII - mock-based)

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T005 [US1] `tests/unit/test_tools_executor.py` + FakeTool 测试基建（research D9：可编程挂起/前 k 次 ToolTransientError/ValidationError/非幂等标注）：逐中间件断言——超时上限内返回 error_class=timeout（SC-002）、瞬时故障 attempts=2 重试成功（SC-003）、非幂等失败 attempts=1 零重发（SC-004）、非法参数进实现前拦截 error_class=validation、未预期异常包装 ToolExecutionError 全字段（tool_name/error_class/attempts/duration_ms/trace_id）、失败不抛出仅返回 failed 记录（SC-008）；另含 executor 包装 hybrid_search 的透传断言（同 ctx/args 结果与直调一致，SC-001）

### Implementation for User Story 1

- [x] T006 [US1] `src/tools/executor.py` 实现 `ToolExecutor.execute(call, ctx) -> ToolExecutionRecord`：固定阶段「权限（required_scopes ⊆ ctx.scopes → error_class=permission）→ 参数校验（args_model.model_validate → validation）→ 幂等判断（idempotent=False 零重试）→ 执行 → 超时（asyncio.wait_for，research D2）→ 重试（timeout/transient 可重试、退避 base×2^n，research D3）→ 异常恢复（统一 ToolExecutionError）」（FR-004~008）；`ToolExecutionRecord` 按 data-model.md 字段与不变式
- [x] T007 [US1] `src/agent/nodes/tool_node.py` 改造：单调用路径经 executor（替换直接 invoke），tool_results/evidence entry 换为 ToolExecutionRecord 序列化（ok/error/hits/top_score 字段名兼容 002），`tool_call` SSE 事件增可选 attempts/duration_ms（contracts v2.1）；`src/agent/graph.py` make_tool_node 注入 executor

**Checkpoint**: 执行引擎 MVP——既有集成测试（含 mock 检索路径）全绿，工具调用自带全部健壮性

## Phase 4: User Story 2 - 权限收敛 (Priority: P1)

**Goal**: 工具面由请求 scopes 驱动，越权调用执行前被拒（FR-003）

**Independent Test**: `uv run pytest tests/unit/test_registry_scopes.py -q` 全绿（SC-005 断言）

### Tests for User Story 2 (REQUIRED by Constitution VII - mock-based)

- [x] T008 [P] [US2] `tests/unit/test_registry_scopes.py`：scopes=["retrieval:read"] → 面含 hybrid_search/doc_reader；scopes=["misc:calc"] → 面不含检索工具；scopes=[] → 空面 + 既有降级路径；executor 对未授权工具直调 → error_class=permission 且工具实现未执行（FakeTool 断言调用计数=0）；部分覆盖（所需 scopes 超集不相等）视为无权（FR-003/Edge）

### Implementation for User Story 2

- [x] T009 [US2] `src/agent/state.py` AgentState 增 `scopes: list[str]` 输入字段；`src/api/routes/chat.py` 从 JWT claims `scopes` 解析注入（缺省/缺字段回退 ["retrieval:read"]，请求体仍禁收 tenant_id/scopes，章程 V）；graph 运行时把 state.scopes 传入 ToolContext.scopes
- [x] T010 [US2] `src/agent/nodes/plan.py` 工具面改由 `registry.visible_tools(state["scopes"])` 生成（替换硬编码 ["retrieval:read"]，research D4）；空面时 plan 走既有「无工具可用」降级口径（不新错误码）

**Checkpoint**: 模型只见、只调它有权的工具；既有 100 项测试（默认 scopes 回退）全绿

## Phase 5: User Story 3 - doc_reader 兑现 (Priority: P2)

**Goal**: 按 doc_id/sec_no 精读条款原文，租户隔离（FR-012）

**Independent Test**: `uv run pytest tests/integration/test_doc_reader.py -q` 全绿（播种库三场景）

### Tests for User Story 3 (REQUIRED by Constitution VII - mock-based)

- [x] T011 [P] [US3] `tests/integration/test_doc_reader.py`（播种库，章程 VII）：doc_id → 全部章节按 sec_no 有序且 sections[].text 为播种原文；(doc_id, sec_no) → 仅指定章节；跨租户 doc_id → not_found；sec_no 不存在 → section_not_found 不回退全文（FR-012/Edge）；title 取 chunk.meta['title'] 回退文档 title+sec_no（research D6）

### Implementation for User Story 3

- [x] T012 [US3] `src/tools/doc_reader.py` 实现 invoke：查 `chunk where doc_id=? and tenant_id=? and chunk_type='parent'`（+sec_no 过滤），Document 表取 title（租户过滤），组装 DocReaderResult（契约不变，research D6）；声明 policy（timeout_ms=5000，data-model.md 表）；`src/agent/graph.py` build_tool_registry 注册 doc_reader

**Checkpoint**: 工具面两项齐备（hybrid_search + doc_reader），章程 I 完整兑现

## Phase 6: User Story 4 - 并行执行 (Priority: P2)

**Goal**: 计划步多调用并行、结果保序、部分失败不连带（FR-011，clarify Q1）

**Independent Test**: `uv run pytest tests/integration/test_parallel_step.py -q` 全绿（SC-006/008 断言）

### Tests for User Story 4 (REQUIRED by Constitution VII - mock-based)

- [x] T013 [P] [US4] `tests/integration/test_parallel_step.py`：一步两调用（各 sleep 0.2s 的 FakeTool）墙钟 <0.3s 且结果顺序=calls 顺序（SC-006）；含非 parallel_safe 调用 → 整步串行；其一失败 → 成功结果保留、失败单独 failed、另一调用不被取消（FR-011/Edge）；无 calls 键的旧 plan dict → 走单调用路径（checkpoint 兼容，data-model.md）

### Implementation for User Story 4

- [x] T014 [US4] `src/agent/nodes/plan.py` PlanStep 增可选 `calls: list[dict] | None`（≤4，research D5）；`src/agent/prompts.py` SYSTEM_PLANNER 增多调用措辞（独立子问题合并同一步，tools 占位符机制不变）
- [x] T015 [US4] `src/tools/executor.py` 增 `execute_step(calls, ctx)`（全 parallel_safe → gather 保序合并；否则串行，research D2/D5）；`src/agent/nodes/tool_node.py` step.calls 存在时走 execute_step、每调用一条 tool_call 事件（同 step，contracts v2.1）

**Checkpoint**: 并行收益可测；旧检查点/单调用计划行为不变

## Phase 7: User Story 5 - MCP 统一调度 (Priority: P2)

**Goal**: 外部 MCP Server 工具动态注册、同管道调度、来源故障降级（FR-002/013/014/015）

**Independent Test**: `uv run pytest tests/unit/test_mcp_client.py -q` 全绿（SC-007 mock 层）

### Tests for User Story 5 (REQUIRED by Constitution VII - mock-based)

- [x] T016 [P] [US5] `tests/unit/test_mcp_client.py`（mcp SDK in-memory transport pair，无子进程，research D7/D9）：list_tools → 动态注册 mcp.<name>（inputSchema 转 args_model，FR-013）；与内置工具重名 → 命名空间隔离不覆盖（FR-002/Edge）；Server 不可达 → 该来源不注册 + 不抛出（FR-014）；清单含非法 schema → 跳过该工具其余正常（Edge）；调用经 executor 同管道（error_class/attempts 口径与内置一致，FR-015）

### Implementation for User Story 5

- [x] T017 [US5] `src/tools/mcp_client.py`：stdio ClientSession 连接、list_tools 动态注册进 Registry（mcp. 前缀、required_scopes 取 settings.mcp_tool_scopes、policy 缺省值）、连接生命周期挂 FastAPI lifespan（startup 连接注册 / shutdown 关闭）、来源不可达 warning 降级（research D7）
- [x] T018 [US5] `scripts/mcp_demo_server.py`：FastMCP 演示 Server，两个工具（保费试算 / 等待期计算，docs/04 §4.5「按场景加入」演示），`--version` 自检参数（quickstart 前置）

**Checkpoint**: MCP 工具与内置工具同一注册表、同一管道、同一事件契约

## Phase 8: Polish & Cross-Cutting Concerns

- [x] T019 [P] `scripts/demo_tool_engine.py`：零 LLM 端到端演示（quickstart 验证 2~6 的载体）——scenario pipeline / scopes / parallel / doc_reader / mcp 五场景，逐行断言式输出（research D9）
- [x] T020 全量回归与验收：`uv run pytest -q` 全绿（既有 100 项零修改 + 本阶段新增）+ 按 `specs/003-tool-engine/quickstart.md` 验证 1~7 逐项通过（SC-001~008 的 mock/CI 口径；真实端到端按 clarify Q2 留 002-T045 债务批次）
- [x] T021 [P] 文档同步：`docs/09-里程碑路线图与验收标准.md` 阶段 3 状态更新（实现完成，验收口径标注）；`docs/04-Tool层设计.md` 如实现与设计有偏差处回写；重建合并文档 + 一致性检查（章程「文档变更后 MUST 重建合并文件」）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1（Setup）**: 无依赖，T001/T002 互不相干文件可并行
- **Phase 2（Foundational）**: 依赖 Phase 1（T003 用到 T002 的 Settings 默认值）；T004 依赖 T003 —— 阻塞全部 US
- **Phase 3~7（US1~US5）**: 均依赖 Phase 2；US2~US5 的 executor 相关部分依赖 US1 的 T006（管道本体）
- **Phase 8（Polish）**: T019 依赖 US1~US5 全部；T020 依赖全部；T021 仅依赖实现定稿

### User Story Dependencies

- **US1（P1）**: Phase 2 后即可开始，无跨故事依赖 —— MVP
- **US2（P1）**: 权限校验落点在 executor（T006 已含 permission 阶段），T009/T010 仅接线 —— 依赖 US1
- **US3（P2）**: 依赖 Phase 2（policy 声明）；executor 注册非必须但按序在 US1 后最稳
- **US4（P2）**: 依赖 US1（execute_step 建立在 execute 之上）；与 US3 文件不相交，可并行
- **US5（P2）**: 依赖 US1（同管道调度）；与 US3/US4 可并行

### Within Each User Story

- Tests MUST be written and FAIL before implementation (mock-based, per Constitution Principle VII)
- 契约/基建（base、FakeTool）先于管道，管道先于接线（tool_node/graph）
- Story complete before moving to next priority

### Parallel Opportunities

- Phase 1: T001/T002 并行
- Phase 4~7 跨故事: US3（doc_reader）↔ US4（并行）↔ US5（MCP client/server）文件互不相交，可并行推进（单人开发则按优先级串行）
- 各 US 内标记 [P] 的测试任务可与后续实现任务的准备工作并行

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1（执行引擎管道 + tool_node 接线）
4. **STOP and VALIDATE**: `pytest tests/unit/test_tools_executor.py` + 既有回归全绿
5. 此时工具调用已自带全部健壮性保障，可独立交付演示

### Incremental Delivery

1. Setup + Foundational → 契约层就绪
2. +US1 执行引擎 → MVP（健壮性中间件生效）
3. +US2 权限收敛 → scopes 驱动工具面
4. +US3 doc_reader → 工具面完整、检索质量增益
5. +US4 并行 → 多调用延迟收益
6. +US5 MCP → 外部工具统一调度（阶段 3 验收四项齐）
7. Phase 8 演示物 + 全量验收 + docs 同步

### 单人执行建议（当前 1 人开发）

按 Phase 串行推进（US3/US4/US5 如需可穿插）；每个 Checkpoint 提交一次；
T020 全量回归作为阶段 3 合入门禁（章程 II 的 mock 层口径）。

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- 红线重申（spec/clarify）：hybrid_search invoke 零改动（T004 仅加声明）；检索 SQL 零改动；
  图拓扑与 SSE 事件序不变（仅可选字段）；既有 100 项测试零修改全绿是 T020 门禁
- 真实 API 相关验收（检索层 bit 级对比、端到端评测）不在本阶段——按 clarify Q2 留 002-T045 批次
