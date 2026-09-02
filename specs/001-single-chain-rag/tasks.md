# Tasks: 单链路 RAG 问答（入库 → 检索 → 带引用回答）

**Input**: Design documents from `/specs/001-single-chain-rag/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Constitution Principle VII (Test Discipline) requires test tasks for every feature — include mock-based unit/integration tests (no real external services: LLM/Embedding/Rerank/Storage); only offline evaluation and manual acceptance may call real services.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Include exact file paths in descriptions

## Path Conventions

- 单项目结构（plan.md 已裁定）：`src/` + `tests/` + `scripts/` 于仓库根
- 本阶段目录集：`src/{api,rag,services,data,security}` + `scripts/` + `tests/{unit,integration,contract}`

<!-- 生成说明：以下任务由 /speckit-tasks 依据 spec（4 个用户故事）、plan（结构/选型）、
     contracts（三接口）、data-model（三实体）、research（D1~D11）生成；按章程原则 VII
     每个故事附带 mock 化测试任务。 -->

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 项目初始化与可运行骨架

- [x] T001 初始化 uv 项目：创建 `pyproject.toml`（Python 3.12，依赖 fastapi、uvicorn、sqlalchemy[asyncio]、asyncpg、pgvector、jieba、pyjwt、pydantic-settings、httpx；dev 依赖 pytest、pytest-asyncio、aiosqlite 不需要——集成测试用真实 PG）与 `src/__init__.py`
- [x] T002 创建 `docker-compose.yml`（服务：postgres 使用 pgvector/pgvector:pg16 镜像含健康检查 + api；卷挂载 `.env`）与 `.env.example`（LLM_BASE_URL/LLM_API_KEY/LLM_MODEL/JWT_SECRET/DATABASE_URL）
- [x] T003 [P] 创建 `src/data/db.py`：async engine/session 工厂（读 DATABASE_URL），提供 `create_all()` 开发期建表入口
- [x] T004 [P] 创建 `src/config.py`：pydantic-settings 配置（embedding 维度 1024、rerank 拒答阈值初值 0.35、top_k 50 / rerank_top_k 5、链路超时 20s、JWT 过期 24h）

**Checkpoint**: `docker compose up -d` 后 api 可启动并连接 PG（`/health` 返回 ok——在 T014 前允许用启动日志验证）

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 全部用户故事依赖的模型、契约客户端、鉴权

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T005 创建 `src/data/models.py`：SQLAlchemy ORM 三表 document/chunk/runtime_log，字段与约束按 `specs/001-single-chain-rag/data-model.md`（含 content_hash、version、visibility/region/expire_at 预留、status CHECK、embedding VECTOR(1024)、tsv TSVECTOR、父子自关联、级联删除）
- [x] T006 [P] 创建 `src/data/dao.py`：会话级租户过滤数据访问层——所有查询强制注入 `tenant_id`（章程 V）；提供 `get_document/create_chunk_batch/get_chunks_by_parent` 等最小方法集
- [x] T007 创建 `src/services/clients/embedding.py`：`EmbeddingClient` Protocol（`embed(texts: list[str]) -> list[list[float]]`，1024 维）+ 百炼 OpenAI 兼容实现（research D2/D6，超时 5s + 仅 5xx/超时指数退避重试 ≤2 次）
- [x] T008 [P] 创建 `src/services/clients/rerank.py`：`RerankClient` Protocol（`rerank(query, docs) -> list[float]`）+ 百炼实现（research D4，重试策略同 T007）
- [x] T009 [P] 创建 `src/services/clients/llm.py`：`LlmClient` Protocol（`stream_chat(messages) -> AsyncIterator[str]`）+ 百炼 chat 实现（SSE 增量，research D6）
- [x] T010 [P] 创建 `tests/unit/fakes.py`：`FakeEmbedding`（文本哈希→确定性 1024 维向量）、`FakeRerank`（可编程分数序列）、`FakeLLM`（按脚本吐 delta）——契约签名与真实实现一致（章程 VII mock 基准）
- [x] T011 创建 `src/security/jwt.py`：HS256 校验（PyJWT，claims tenant_id/scopes/exp）+ FastAPI 依赖 `get_tenant_context`；无效/缺失令牌 401（FR-012，ADR-009）
- [x] T012 [P] 创建 `scripts/issue_token.py`：读 `.env` JWT_SECRET 签发演示令牌并打印（quickstart 前置）
- [x] T013 创建 `src/api/main.py`：FastAPI 应用工厂（挂载 JWT 依赖、`/v1/health`、路由注册点），`src/api/schemas.py`：ChatRequest 等请求/响应模型（FR-011 三接口边界，无会话字段）
- [x] T014 集成测试基座：`tests/integration/conftest.py`——测试库 schema 建立与清理 fixture、`tests/conftest.py`——fake 客户端注入 app 的 fixture（不产生真实外部调用）

**Checkpoint**: Foundation ready - user story implementation can now begin

---

## Phase 3: User Story 1 - 提问获得带引用的回答 (Priority: P1) 🎯 MVP

**Goal**: 在已有索引数据上，自然语言提问返回带 `[n]` 引用的流式回答，引用可展开条款原文

**Independent Test**: 向测试库直接播种（seed）父子块（绕过上传链路），调用 `POST /v1/chat`，
断言 SSE 事件序与引用一致性（contracts/api.md §3）

### Tests for User Story 1 (REQUIRED by Constitution VII - mock-based)

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T015 [P] [US1] 单元测试：RRF 融合正确性（两路排名→融合序、k=60、去重）于 `tests/unit/test_hybrid.py`
- [x] T016 [P] [US1] 单元测试：引用组装——`[n]` 与 citations 一一对应、quote 是 chunk.text 原文子串（空白规范化容错）于 `tests/unit/test_citation.py`
- [x] T017 [P] [US1] 契约测试：`POST /v1/chat` SSE 事件序 `evidence→answer*→citations→done`、事件字段完整性、401 无令牌、422 空问题、空库/检索不可用 503（FR-010），于 `tests/contract/test_chat_api.py`（httpx ASGI + fake 客户端）
- [x] T018 [US1] 集成测试：播种数据→提问→断言命中子块所属父块、答案含引用、runtime_log 落一行（FR-013）；含口语化提问用例（「买的保险多久才开始生效能赔」→ 命中等待期条款，FR-005），于 `tests/integration/test_ask_flow.py`

### Implementation for User Story 1

- [x] T019 [US1] 实现混合检索 `src/rag/hybrid.py`：向量路（pgvector HNSW + iterative_scan + 3× 超采样，research D1）+ 关键词路（jieba 分词→tsquery，ts_rank_cd）→ RRF 融合 top-50（`SELECT` 强制 tenant 过滤）；函数保持纯函数签名（显式入参/出参，无全局状态），便于阶段 2 被 Tool Registry 直接包装（原则 I 豁免的兑现前提）
- [x] T020 [P] [US1] 实现重排与阈值 `src/rag/rerank.py`：调用注入的 RerankClient 精排取 top-5，返回 top_score 供拒答判定（research D4）
- [x] T021 [US1] 实现问答编排 `src/services/answer.py`：检索→重排→引用组装（doc title + sec_no + 原文 quote 截取）→ LlmClient 流式生成（防编造 system prompt：只依据证据、标注 [n]）→ evidence/answer/citations/done 事件生成器（contracts/api.md §3）
- [x] T022 [US1] 实现路由 `src/api/routes/chat.py`：POST /v1/chat（JWT 依赖、question 校验 422、空库/检索不可用返回 503 与明确提示（FR-010）、StreamingResponse text/event-stream、链路 20s 熔断——超时发 refused done 事件，章程 IV）
- [x] T023 [US1] 实现 `src/services/runtime_log.py` 并在 answer 完成路径写入一行（trace_id 生成 `tr-<hex>`、hit_count、latency_ms、refused、top_score）

**Checkpoint**: 播种数据上提问可收到带引用流式回答（fake 客户端下全链路可断言）

---

## Phase 4: User Story 2 - 管理员上传条款并自动可检索 (Priority: P1)

**Goal**: 上传文档自动解析切分索引，5 分钟内可检索；同指纹去重；失败可重试

**Independent Test**: 上传 `data/corpus/` 样例文档→状态转 indexed→对文档独有内容提问命中；
重复上传返回既有文档；损坏文件转 failed 且可重试

### Tests for User Story 2 (REQUIRED by Constitution VII - mock-based)

- [x] T024 [P] [US2] 单元测试：解析器——章节编号识别（`1 / 1.1 / 2.3.1`）、表格行切分、空文件/无章节文本降级，于 `tests/unit/test_parser.py`（样例取自 `data/corpus/` 截选）
- [x] T025 [P] [US2] 单元测试：父子切分不变式——子块 50~1200 字符、10% 重叠、父块=完整小节、子块必有 parent_id，于 `tests/unit/test_chunker.py`（data-model.md 不变式）
- [x] T026 [P] [US2] 契约测试：上传 202→status 轮询 indexed；同指纹重传 200 + `X-Deduplicated: true`；跨租户 doc_id 404；failed→reprocess 202，于 `tests/contract/test_documents_api.py`
- [x] T027 [US2] 集成测试：上传→indexed→US1 问答命中该文档（fake Embedding 下哈希向量可回归）于 `tests/integration/test_ingest_flow.py`

### Implementation for User Story 2

- [x] T028 [US2] 实现解析器 `src/rag/parser.py`：轻量结构解析（章节编号正则、表格行识别、结构化 Markdown 中间表示，research D5；PDF 返回明确不支持错误→415 由路由层处理）
- [x] T029 [P] [US2] 实现切分器 `src/rag/chunker.py`：父块=章节小节、子块 300~500 token/10% 重叠、表格按行（FR 与 data-model 不变式）
- [x] T030 [US2] 实现索引器 `src/rag/indexer.py`：jieba 预分词→tsvector(simple)、EmbeddingClient 批量向量、批量写 chunk（父块行+子块行）、(doc_id, version) 幂等清理旧版本块
- [x] T031 [US2] 实现入库编排 `src/rag/pipeline.py`：processing→indexed/failed 状态机 + asyncio.create_task 后台执行（research D10）+ 失败 error 记录
- [x] T032 [US2] 实现路由 `src/api/routes/documents.py`：POST /v1/documents（multipart、SHA-256 指纹、同指纹 200 去重、PDF 415）、GET /v1/documents/{id}/status、POST /v1/documents/{id}/reprocess（contracts/api.md §1/§2）

**Checkpoint**: 上传→自动入库→问答命中全链路可独立验证

---

## Phase 5: User Story 3 - 证据不足时拒答而非编造 (Priority: P2)

**Goal**: top_score 低于阈值或零命中时走拒答路径，返回改进建议且不生成编造内容

**Independent Test**: 以低分配置的 FakeRerank 调用问答，断言 refused=true、citations=[]、
建议文案存在

### Tests for User Story 3 (REQUIRED by Constitution VII - mock-based)

- [x] T033 [P] [US3] 单元测试：拒答判定——阈值边界（等于/低于）、零命中、改进建议文案生成，于 `tests/unit/test_refusal.py`
- [x] T034 [US3] 契约+集成测试：拒答 SSE 序（answer 单 delta→citations 空→done refused=true）、runtime_log.refused=true，于 `tests/contract/test_chat_refusal.py` 与 `tests/integration/test_refusal_flow.py`

### Implementation for User Story 3

- [x] T035 [US3] 扩展 `src/services/answer.py`：拒答分支——top_score < config 阈值或 hit_count=0 时不调 LLM，直接输出拒答与建议（引用库里存在的术语示例，如「等待期」「宽限期」），done 事件 `refused=true`（FR-008，章程 IV）
- [x] T036 [P] [US3] 扩展 `src/api/routes/chat.py`：拒答路径复用同一 SSE 管道（contracts/api.md §3 拒答示例）

**Checkpoint**: 拒答路径可独立验证，答案永远有据（章程 IV）

---

## Phase 6: User Story 4 - 维护者运行检索质量评测 (Priority: P2)

**Goal**: 一条命令跑闭卷集评测，产出 Recall@5（整体+分难度）与 L4 拒答率报告及失败案例清单

**Independent Test**: 对播种的小型 fixture 数据集运行评测脚本，断言报告结构与已知指标值

### Tests for User Story 4 (REQUIRED by Constitution VII - mock-based)

- [x] T037 [P] [US4] 单元测试：命中判定——quote 规范化空白后在 top-5 父块文本中即命中、未命中记录失败案例，于 `tests/unit/test_eval_matching.py`

### Implementation for User Story 4

- [x] T038 [US4] 实现评测脚本 `scripts/run_retrieval_eval.py`：读 `data/evals/golden_qa_kaihe.clean.jsonl`（985+51），逐题检索（无 LLM 调用，research D9）、整体与 L0/L1/L3 分难度 Recall@5、L4 拒答率、失败案例清单（问题/期望 quote/实际 top5）、`--top-k/--output/--limit/--calibrate`（用 L4 子集扫阈值）参数
- [x] T039 [P] [US4] 评测 fixture：`tests/fixtures/eval_mini.jsonl`（10 条微型集）+ `tests/integration/test_eval_script.py`——对播种库跑脚本断言报告结构与指标可复现

**Checkpoint**: 验收门禁可执行：Recall@5 ≥ 0.8 与拒答率 ≥ 0.9 可出报告（SC-001/002）

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 跨故事收尾与验收

- [x] T040 [P] 更新 `prototype/index.html` 端点为本地服务并人工核对事件对接（仅改地址与鉴权头，UI 不动）
- [ ] T041 [P] 校准拒答阈值：用 `--calibrate` 对 L4 子集扫参，将满足 SC-002 ≥ 0.9 的值写回 `src/config.py` 默认值并在 config 注释记录校准日期
- [x] T042 [P] 文档同步：docs/09 阶段 1 验收清单勾选更新 + 运行 `python scripts/build_combined_doc.py` 重建合并文件
- [x] T043 一致性检查：执行 docs 修订验证 grep 断言（无请求体 tenant_id、无真实服务调用进测试、SSE 事件序与契约一致）并记录输出
- [ ] T044 质量抽检：从闭卷集抽 20 条 L0 问题实测——端到端延迟 P95 ≤ 8s（SC-003），并逐条人工核对引用原句与答案结论，一致率须 100%（SC-006），结果附入 PR 描述
- [ ] T045 全量回归：`uv run pytest -q` 全绿 + `scripts/run_retrieval_eval.py` 达门槛，对照 `specs/001-single-chain-rag/quickstart.md` 逐项通过
- [x] T046 [P] 创建 CI 门禁 `.github/workflows/eval-gate.yml`：两个 job——① `pytest -q`（services 起 pgvector/pgvector:pg16，纯 mock 测试）；② eval-gate（secrets 注入 `LLM_API_KEY`，播种 102 份条款→跑 `scripts/run_retrieval_eval.py`，Recall@5 < 0.8 即失败阻断）。仅 Embedding 真实调用、无 LLM 生成（约千块级，成本可忽略），符合章程 II/III 边界

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (T001~T004)**: 无依赖，立即可开始
- **Foundational (T005~T014)**: 依赖 Setup；**阻塞全部用户故事**（模型/客户端/JWT 是公共底座）
- **US1 (T015~T023)**: 依赖 Foundational；用播种数据，**不依赖 US2**
- **US2 (T024~T032)**: 依赖 Foundational；T027 依赖 US1 的问答路径（复用 hybrid）
- **US3 (T033~T036)**: 依赖 US1（扩展 answer.py 拒答分支）
- **US4 (T037~T039)**: 依赖 US1/US2（评测走真实检索链路）；可在 US3 前并行
- **Polish (T040~T045)**: 依赖全部故事完成

### User Story Dependencies

- **US1 (P1)**: Foundational 后即可开始（播种数据独立验证）→ MVP
- **US2 (P1)**: 与 US1 可并行开发；其集成测试 T027 复用 US1 组件，建议 US1 先合
- **US3 (P2)**: 依赖 US1 的 answer.py；工作量小，紧随 US1
- **US4 (P2)**: 依赖 US1+US2 链路；评测脚本本体（T038）可在播种库上先行开发

### Within Each User Story

- 测试先写且先失败（章程 VII）；models→services→routes；核心实现→集成验证

### Parallel Opportunities

- Foundational 内 T006/T008/T009/T010/T012 均 [P]
- US1/US2 两故事在 Foundational 完成后可并行（不同文件）
- 各故事内单测任务（[P]）可并行编写

---

## Implementation Strategy

### MVP First (US1 only)

1. Setup + Foundational 完成
2. US1（T015~T023）→ 播种数据上端到端可问答
3. **STOP and VALIDATE**: 契约/集成测试全绿，SSE 事件序正确
4. 此时 `prototype/` 用播种数据已可演示

### Incremental Delivery

1. +US2 → 真实文档可入库，演示从空库走通全流程
2. +US3 → 拒答防线，可对外承诺「不编造」
3. +US4 → 评测门禁出报告，验收 SC-001/002 达标
4. Polish → 原型对接、阈值校准、性能冒烟、文档同步

### 单人执行建议（当前 1 人开发）

按 MVP 顺序串行：Setup → Foundational → US1 → US2 → US3 → US4 → Polish；
每个 Checkpoint 处提交一次（章程：每逻辑组提交）。

---

## Notes

- 全部测试禁止真实外部服务调用（LLM/Embedding/Rerank/存储）——fake 客户端 + 录制夹具（章程 VII）
- 每个任务含精确文件路径；[P] 任务无文件冲突可并行
- 评测与拒答阈值集中在 `src/config.py`，禁止散落硬编码
- 违反章程的取舍必须记录在 plan.md Complexity Tracking（现仅原则 I 中间态豁免一项）
