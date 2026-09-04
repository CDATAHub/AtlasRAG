# Tasks: AgentLoop 规划式问答（反思回环 · 多轮 · 必收敛）

**Input**: Design documents from `/specs/002-agent-loop/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Constitution Principle VII (Test Discipline) requires test tasks for every feature — include mock-based unit/integration tests (no real external services: LLM/Embedding/Rerank/Storage); only offline evaluation and manual acceptance may call real services.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Include exact file paths in descriptions

## Path Conventions

- 单项目结构（plan.md 已裁定）：`src/` + `tests/` + `scripts/` 于仓库根
- 本阶段新增目录集：`src/agent/`（含 `nodes/`）+ `src/tools/`；改造 `src/{api,services,data}/`
- 测试：`tests/{unit,integration,contract}`；检查点测试用本地测试 PG（测试基座，非外部服务）

<!-- 生成说明：任务依据 spec（5 个用户故事）、plan（结构/选型）、research（D1~D12）、
     data-model（session/message/runtime_log 扩展）、contracts（chat v2 + sessions）生成；
     按章程原则 VII 每个故事附带 mock 化测试任务（先行、先失败）。 -->

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 依赖与配置扩展

- [x] T001 `pyproject.toml` 新增依赖 langgraph（≥1.0）、langgraph-checkpoint-postgres、psycopg[binary]，`uv sync` 锁定；不升级既有依赖
- [x] T002 [P] 扩展 `src/config.py`：max_steps=6、plan_rounds_max=3、token_budget=8000、sliding_window_rounds=6、compress_threshold_tokens=3000、寒暄规则表（问候/致谢/身份询问正则 + ≤30 字符上限，research D8）；既有配置项不动

**Checkpoint**: `uv run python -c "import langgraph"` 通过，服务可启动

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 状态机骨架、工具契约层、LLM 结构化输出、会话/消息模型

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T003 创建 `src/agent/state.py`：AgentState TypedDict 按 docs/03 §3.3——`messages`（add_messages reducer）、`tool_results`（operator.add）、`plan/current_step/route/evidence/draft/reflect_result/final_answer/citations`、控制字段 `steps/plan_rounds/tokens_used/max_steps/token_budget/tenant_id/session_id/client_msg_id/thread_id`
- [x] T004 [P] 创建 `src/tools/base.py`：`Tool` Protocol（name/description/args_model/result_model/scope/`invoke(args) -> result`）+ `Registry`（注册表 + `visible_tools(scopes)` 过滤——模型可见面收敛，章程 I）
- [x] T005 [P] 创建 `src/tools/hybrid_search.py`：包装 `src/rag/hybrid.py` + `src/rag/rerank.py` 纯函数为 `hybrid_search` 工具（入参 query/top_k，出参 hits 列表 Pydantic 模型，签名不变——兑现 T019 阶段 1 承诺）；创建 `src/tools/doc_reader.py`：仅 Pydantic 入出参契约与 NotRegistered 错误语义，不注册（clarify Q1）
- [x] T006 扩展 `src/services/clients/llm.py`：LlmClient Protocol 增非流式 `chat(messages, *, response_format=None) -> LlmResult`（LlmResult = content + usage.tokens）；百炼实现透传 `{"type":"json_object"}`（research D4）；扩展 `tests/unit/fakes.py` FakeLLM：脚本化按消息特征返回预置 PlanResult/ReflectResult JSON、delta 序列、可编程 usage（预算边界用例）
- [x] T007 扩展 `src/data/models.py`：新增 session/message 两表 + runtime_log 扩展列（session_id/message_id/client_msg_id/plan_rounds/steps/tokens_used/convergence_reason），约束与状态机按 `specs/002-agent-loop/data-model.md`（含 UNIQUE(tenant_id,session_id,client_msg_id)、status CHECK、expire_at/deleted_at 预留）
- [x] T008 [P] 扩展 `src/data/dao.py`：会话/消息租户过滤方法集——create_session/get_session(含 deleted_at 过滤)/set_session_status/append_message/get_messages/soft_delete_session；全部强制 tenant_id 注入（章程 V）
- [x] T009 创建 `src/agent/graph.py`：`build_graph(checkpointer)` 六节点占位接线（plan→route→tool_node→generate→reflect→converge，含 tool_node→route 回环占位）；改造 `src/api/main.py` lifespan：AsyncPostgresSaver.from_conn_string 进入长生命周期 + `setup()` 建表（research D2）；扩展 `tests/conftest.py`：注入 FakeLLM 脚本与本地测试 PG 检查点器 fixture

**Checkpoint**: 图可编译、checkpointer 建表成功、`uv run pytest -q` 既有 53 项仍全绿（阶段 1 行为不回归）

---

## Phase 3: User Story 1 - 复杂问题按计划拆解执行，回答带引用 (Priority: P1) 🎯 MVP

**Goal**: 条款类问题先产出可观测计划再逐步检索，答案带引用；寒暄走模板快路径；常识直答无工具调用

**Independent Test**: 播种库上提出含两个子任务的问题，SSE 出现 `plan`（steps≥2）→ `tool_call*` → `evidence` → `answer*` → `citations` → `done`，答案同时覆盖两个子问题且引用可展开（contracts/api.md §1）

### Tests for User Story 1 (REQUIRED by Constitution VII - mock-based)

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T010 [P] [US1] 契约测试 `tests/contract/test_chat_agent_loop.py`：事件序与 done 扩展字段（session_id/message_id/client_msg_id/convergence_reason/rounds/steps/tokens_used）、寒暄快路径（无 plan/tool_call、tokens_used=0、首字节计时断言放宽为路径断言）、常识直答（plan 有 route=answer、无 tool_call）、401/422/503 沿用（httpx ASGI + FakeLLM 脚本）
- [x] T011 [P] [US1] 单元测试 `tests/unit/test_plan_route.py`：PlanResult 解析（合法 JSON/非法 JSON 重试 1 次→降级单步计划）、route 确定性规则三分支（route=answer→generate、计划未完→tool_node、计划完→generate）、plan prompt 含会话历史与改写要求

### Implementation for User Story 1

- [x] T012 [US1] 实现 `src/agent/nodes/plan.py`：PlanResult/PlanStep Pydantic 模型；结构化输出调用（T006 chat + json_object）、解析失败重试 1 次后降级「单步检索」计划（spec Edge）；plan 事件经 stream writer 外发（含 steps/rationale，research D3/D4）
- [x] T013 [P] [US1] 实现 `src/agent/nodes/route.py`：纯确定性路由（无 LLM）——route==answer→generate；计划未执行完→tool_node（取 plan[current_step]）；计划执行完→generate（research D1；docs/03 §3.4.2 规则）
- [x] T014 [US1] 实现 `src/agent/nodes/tool_node.py`：按 plan[current_step] 经 Registry 调用工具，结果 append `tool_results` 并筛选入 `evidence`（父块文本+来源+得分）；失败记入 evidence 标记 failed 不中断（FR-004）；`tool_call`/`evidence` 事件外发；current_step 前进
- [x] T015 [US1] 实现 `src/agent/nodes/generate.py`：证据 prompt 组装（防编造 + [n] 标注，沿用阶段 1 口径）→ stream_chat delta 经 writer 外发 `answer_delta`；draft/citations 组装复用阶段 1 引用逻辑（`src/services/answer.py` 中抽取为纯函数，二者共用）
- [x] T016 [US1] 实现 `src/agent/nodes/reflect.py`（最小版：硬规则 steps≥max_steps 强制收敛 + 证据为空→拒答判定）与 `src/agent/nodes/converge.py`（final_answer/citations 组装 + convergence_reason ∈ natural|refused）
- [x] T017 [US1] 改造 `src/services/answer.py`：寒暄规则命中→API 层模板短路（零 LLM/检索，research D8）；缺省创建会话并落 message 行（FR-009/FR-010 最小写入）；graph.astream(stream_mode="custom") 执行与事件→SSE 转发；拒答路径沿用阶段 1 管道
- [x] T018 [US1] 改造 `src/api/routes/chat.py` + `src/api/schemas.py`：ChatRequest 增可选 session_id/client_msg_id（422 校验沿用 ≤500 字符）；SSE 事件模型含 plan/tool_call；错误码 401/422/503 不变（contracts/api.md §1）
- [x] T019 [US1] 集成测试 `tests/integration/test_agent_loop_flow.py`：播种库多子任务问题→plan.steps≥2、答案覆盖两子问题、引用原文一致；runtime_log 落 plan_rounds/steps/tokens_used；阶段 1 既有问答路径回归不破

**Checkpoint**: US1 独立可用——单轮规划式问答 + 快路径 + 直答全通，阶段 1 行为无回归

---

## Phase 4: User Story 2 - 证据不足时反思回环，重规划只替换未执行步骤 (Priority: P1)

**Goal**: 反思判定不足时自动改写/补检/换工具，保留已执行步骤与证据，≤3 轮强制收敛

**Independent Test**: 同配置首轮未命中案例提问出现 round≥2 的 plan/evidence 且最终命中；持续无命中问题 3 轮内 refused 收敛（spec US2 Independent Test）

### Tests for User Story 2 (REQUIRED by Constitution VII - mock-based)

- [ ] T020 [P] [US2] 单元测试 `tests/unit/test_reflect_loop.py`：ReflectResult 解析与 next_action 映射（continue_plan→route；retrieve_more/rewrite_query/switch_tool→plan）、硬规则优先（plan_rounds≥3 强制 converge）、重规划「只替换未执行步骤」不变式（已执行 plan 步骤与 tool_results 保留）
- [ ] T021 [P] [US2] 契约测试 `tests/contract/test_chat_loop.py`：回环事件序（plan/evidence 重复出现、round 递增、≤3）、3 轮后 refused done（convergence_reason 正确）、续计划路径不触发新 plan 事件

### Implementation for User Story 2

- [ ] T022 [US2] 扩展 `src/agent/nodes/reflect.py`：LLM 充分性判据（结构化 ReflectResult：sufficient/reason/next_action/next_query）+ 硬规则先行（research D9）；改造 `src/agent/nodes/plan.py`：接收反思反馈重规划——保留 current_step 之前的步骤、只追加/替换未执行部分（FR-005）
- [ ] T023 [US2] 扩展 `src/agent/graph.py`：reflect 条件边 {converge, continue_plan→route, 回环→plan} 接线 + plan_rounds 计数上限判定（FR-006）
- [ ] T024 [P] [US2] 扩展 `scripts/run_retrieval_eval.py`：`--loop` 模式——首轮（无 LLM）统计失败集 → 仅失败集开回环真调 LLM 重跑 → 报告增 repair_rate（修复数÷同配置首轮失败数）与修复案例清单（clarify Q4，research D11）
- [ ] T025 [US2] 集成测试 `tests/integration/test_loop_flow.py`：FakeRerank 低分→改写后高分脚本驱动回环修复；持续无命中 3 轮拒答；runtime_log.plan_rounds 与事件 round 一致

**Checkpoint**: US1+US2 可用——回环修复路径与强制收敛全通，首轮评测指标不回退

---

## Phase 5: User Story 3 - 必收敛三重保险：任何输入都有限步停止 (Priority: P2)

**Goal**: steps/预算/超时三保险独立生效，降级输出不编造，收敛原因全程可审计

**Independent Test**: 对抗输入（诱导循环/持续无命中/可编程超预算/慢响应 fake）逐一提交，全部上限内确定性停止且 done.convergence_reason 正确（spec US3 Independent Test）

### Tests for User Story 3 (REQUIRED by Constitution VII - mock-based)

- [ ] T026 [P] [US3] 单元测试 `tests/unit/test_guards.py`：步数硬停（steps≥6）、预算记账（usage 累加≥8000 拒发下一次 LLM 调用）、超时熔断（asyncio.timeout 触发降级）、convergence_reason 枚举完备性
- [ ] T027 [P] [US3] 契约测试 `tests/contract/test_chat_convergence.py`：诱导循环输入→done(max_steps)；FakeLLM 超大 usage→done(budget)；慢速 fake→done(timeout) 且连接不断、refused=false 语义与降级提示（FR-007）

### Implementation for User Story 3

- [ ] T028 [US3] 实现 `src/agent/guards.py`：token 记账器（usage 逐次累加 + 预算判定，research D9）、max_steps 判定、供 route/reflect 调用的「硬规则先行」检查函数；接线进图执行路径
- [ ] T029 [US3] 贯通收敛原因：`src/agent/nodes/converge.py` 与 `src/services/answer.py` 熔断降级路径（asyncio.timeout 包裹图执行，超时发降级 done 不断连）→ done 事件与 runtime_log.convergence_reason 一致落库（FR-015/016）
- [ ] T030 [US3] 集成测试 `tests/integration/test_adversarial_flow.py`：对抗用例集（内联 fixtures：诱导循环/持续无命中/超预算/慢响应）逐条断言确定性停止（steps≤6、tokens_used≤8000、latency<熔断上限）、零半截答案、档案可证（SC-003 的 mock 化子集；100 条全量实测留 T045）

**Checkpoint**: US1+US2+US3 可用——任何输入确定性停止，收敛原因可审计

---

## Phase 6: User Story 4 - 多轮对话与会话管理 (Priority: P2)

**Goal**: 会话历史支撑指代消解与压缩（证据链保留），同会话串行 409、幂等重放、历史查询/软删

**Independent Test**: 首轮问 A 产品等待期、追问「它的宽限期呢」正确命中同产品条款；历史可查询；删除后 404（spec US4 Independent Test）

### Tests for User Story 4 (REQUIRED by Constitution VII - mock-based)

- [ ] T031 [P] [US4] 契约测试 `tests/contract/test_sessions_api.py`：GET 历史（含 citations、时间有序）、DELETE 204→GET 404、跨租户/不存在 404、409 session_busy、幂等重放（replayed 流 + runtime_log 无新行）（contracts/api.md §2/§3）
- [ ] T032 [P] [US4] 单元测试 `tests/unit/test_context_window.py`：滑窗保留最近 6 轮、>3000 token 触发摘要压缩、压缩后 citations 摘录保留（证据链不压缩）、token 近似（字符÷1.5）边界

### Implementation for User Story 4

- [ ] T033 [US4] 实现 `src/services/sessions.py`：会话生命周期（查询/软删/删除在有进行中请求时延迟生效）、按 session_id 的 asyncio.Lock 串行闸门（409 语义，research D6）、幂等判定（已完成→重放事件流；中断→标记续跑，供 US5）
- [ ] T034 [US4] 实现 `src/services/context_window.py`：滑窗 + 一次 LLM 摘要压缩（压缩对话过程、citations 摘录随摘要保留，research D7）；接入 plan/generate 上下文组装（FR-011/014）
- [ ] T035 [US4] 实现 `src/api/routes/sessions.py`（GET/DELETE + 404 语义）；改造 `src/api/routes/chat.py`：409/幂等重放接入（contracts/api.md §1 幂等语义）
- [ ] T036 [P] [US4] 构造 50 条双轮用例 `tests/fixtures/multi_turn.jsonl`（首问→指代追问→期望命中条款）+ 批量断言脚本/测试（FakeLLM 脚本化指代消解路径），支撑 SC-006 的 mock 化回归（真实 90% 口径实测留 T045）
- [ ] T037 [US4] 集成测试 `tests/integration/test_session_flow.py`：双轮指代消解（FakeLLM 断言 plan 收到历史）、软删后查询/续跑均 404、并发第二问 409、幂等重放一致性、压缩触发后引用仍可展开（SC-007 mock 化）

**Checkpoint**: US1~US4 可用——多轮会话全链路（串行/幂等/压缩/管理）就绪

---

## Phase 7: User Story 5 - 检查点恢复：进程重启后续跑 (Priority: P3)

**Goal**: 进程重启后同 session+client_msg_id 重发可从检查点续跑同一次回答；检查点不可用给明确指引

**Independent Test**: 循环执行中重启进程→重发→续跑完成且已执行检索不重复；删除检查点→明确错误与重新开始指引（spec US5 Independent Test）

### Tests for User Story 5 (REQUIRED by Constitution VII - mock-based)

- [ ] T038 [P] [US5] 集成测试 `tests/integration/test_checkpoint_resume.py`：图执行中取消任务模拟崩溃→以新 app 实例（同测试 PG checkpointer）重发相同幂等键→续跑至 done 且 tool_results 不重复（FakeLLM 断言调用次数）；检查点行删除→明确错误与重试指引、无半截输出（clarify Q2 语义）

### Implementation for User Story 5

- [ ] T039 [US5] 改造 `src/services/answer.py`：中断检测（session status=interrupted 或检查点存在）→ `graph.astream(None, config)` 续跑（research D2）；`src/services/sessions.py`/`src/api/main.py` 启动时遗留 running→interrupted 复位（FR-018）
- [ ] T040 [US5] 检查点不可用错误路径：明确错误码/消息与重新开始指引（US5 场景 2），MUST NOT 输出半截答案

**Checkpoint**: 全部用户故事独立可用——任何中断路径可恢复或明确降级

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: 前端对接、文档同步、CI 与全量验收

- [ ] T041 [P] 增量对接 `prototype/index.html`：渲染 plan/tool_call 事件与 session_id/client_msg_id 透传（仅数据对接，UI 不动）
- [ ] T042 [P] 文档同步：docs/09 阶段 2 验收清单勾选更新 + `python scripts/build_combined_doc.py` 重建合并文件
- [ ] T043 [P] 一致性检查：grep 断言（事件序与 contracts/api.md §1 一致、无请求体 tenant_id、测试路径无真实外部服务调用）并记录输出
- [ ] T044 [P] 更新 `.github/workflows/eval-gate.yml`：PR 门禁维持首轮确定性指标（无 LLM）；新增注释说明 `--loop` 回环评测属 nightly/发布前分层（含 LLM 调用），不在 PR 门禁跑（章程 II 分层口径）
- [ ] T045 全量回归：`uv run pytest -q` 全绿 + 按 `specs/002-agent-loop/quickstart.md` 逐项通过 + SC-003 对抗 100 条实测（≤上限、零半截）+ SC-006 50 条双轮真实跑 ≥90% + SC-004/005/009 延迟抽样实测记录

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，立即开始
- **Foundational (Phase 2)**: 依赖 Setup；**阻塞全部用户故事**
- **US1 (Phase 3)**: 依赖 Foundational——核心 MVP
- **US2 (Phase 4)**: 依赖 US1（reflect/plan/graph 均在其产物上扩展）
- **US3 (Phase 5)**: 依赖 US1（guards 挂接图路径）；与 US2 可并行（不同文件为主，T029 触及 answer.py 需在 T017 后）
- **US4 (Phase 6)**: 依赖 US1（chat 路径与 sessions 模型）；压缩（T034）依赖 US1 上下文组装
- **US5 (Phase 7)**: 依赖 US4（幂等键语义与 sessions 状态机）
- **Polish (Phase 8)**: 依赖全部用户故事完成

### User Story Dependencies

- **US1 → US2 → US4 → US5** 为主线（单轮 → 回环 → 多轮 → 恢复，逐层叠加）
- **US3** 可在 US1 后任意时点插入（建议 US2 后，收敛原因枚举一次性贯通）
- 单人执行按主线顺序推进；US3/US4 内部测试任务可与前置实现并行编写

### Within Each User Story

- 测试先行（Constitution VII）：契约/单元测试先写且先失败
- 节点实现顺序：plan/route（T012/T013）→ tool_node（T014）→ generate（T015）→ reflect/converge（T016）→ 编排与路由（T017/T018）→ 集成（T019）
- 每故事结束跑 Checkpoint 验证独立可用

### Parallel Opportunities

- Phase 2：T004/T005/T008 三组互不相干文件可并行
- 各故事内 [P] 测试任务可与实现并行编写（先失败后转绿）
- T024（评测脚本 --loop）与图实现无文件冲突，可随时并行
- T041~T044 全部 [P]（prototype/docs/CI/一致性检查互不相干）

---

## Implementation Strategy

### MVP First (US1 Only)

1. 完成 Phase 1~2（Setup + Foundational）
2. 完成 Phase 3（US1）
3. **STOP and VALIDATE**：`tests/contract/test_chat_agent_loop.py` + `tests/integration/test_agent_loop_flow.py` 全绿，手跑 quickstart 验证 1
4. 此时已可演示「规划 → 工具调用 → 带引用回答」完整决策流

### Incremental Delivery

1. Setup + Foundational → 基座就绪（阶段 1 测试不回归）
2. +US1 → 规划式单轮 MVP
3. +US2 → 反思回环（效果提升可量化：repair_rate）
4. +US3 → 收敛保险（章程 IV 红线闭环）
5. +US4 → 多轮会话（产品完整度）
6. +US5 → 检查点恢复（可靠性）
7. Polish → 文档/CI/全量验收，对照 docs/09 阶段 2 清单勾选

### 单人执行建议（当前 1 人开发）

按 US1 → US2 → US3 → US4 → US5 顺序；每个 Checkpoint 提交一次；
阶段 1 遗留（T041/T044/T045 of 001）与本阶段 T045 全量回归合并执行以节省真实 API 配额。

---

## Notes

- [P] 任务 = 不同文件、无未完成依赖
- [Story] 标签保证任务可追溯到 spec 用户故事
- 所有 LLM 行为测试走 FakeLLM 脚本（plan/reflect JSON + delta + usage），检查点器连本地测试 PG（测试基座，非外部服务）
- 验证测试先失败再实现；每个 Checkpoint 独立验证该故事
- 避免跨故事同文件冲突：answer.py/chat.py/plan.py/reflect.py/graph.py 的扩展任务已按故事先后排序，严禁并行改同一文件
