<!--
SYNC IMPACT REPORT
==================
版本变更: （模板，无版本） → 1.0.0（首次批准）
修改的原则: 无（初始制定，全部新增；模板含 5 个原则槽位，按项目实际定为 6 项，新增第 VI 项）
新增章节: Core Principles（六项）、技术栈与事实源约束、开发与评测工作流、Governance
删除章节: 无
模板同步状态:
  - .specify/templates/plan-template.md  ✅ 无需更新（Constitution Check 为通用占位，按章程文件运行时填充）
  - .specify/templates/spec-template.md  ✅ 无需更新（无章程专属小节）
  - .specify/templates/tasks-template.md ✅ 无需更新（任务分阶段结构已覆盖章程工作流）
  - .claude/skills/speckit-*.md（10 个）  ✅ 已核对，均为通用引用（.specify/memory/constitution.md 路径未变）
遗留 TODO: 无
-->

# AtlasRAG Constitution

## Core Principles

### I. 检索工具化（Retrieval-as-a-Tool）

- 检索 MUST 以工具身份进入 Tool Registry，由 Agent 决定是否调用、调用几次、何时放弃；
  禁止把检索实现为生成前的固定流水线步骤。
- LLM 可见工具面 MUST 收敛（v1：`hybrid_search`、`doc_reader`，按场景开放
  `sql_query` / `calculator` / `record_confirmation`）；`vector_search` / `bm25_search`
  为内部路径，禁止单独暴露给模型。
- 理由：这是 Agentic RAG 与普通 RAG 的分水岭；工具面收敛避免规划器抖动与重复检索。

### II. 一切皆可评测（Eval-Gated Everything, NON-NEGOTIABLE）

- 任何影响检索或生成的改动 MUST 通过分层门禁方可合并：
  PR = 闭卷集确定性检索指标全量（Recall@k、引用命中率）+ 生成评测分层抽样
  （100~200 条，按难度×险种，temp=0）；nightly / 发布前跑全量。
- 阻断阈值 MUST 为「基线 ±2σ」，σ 来自基线版本实测 run-to-run 方差；
  禁止使用未经方差校准的固定百分比阈值。
- 效果结论 MUST 由评分与失败案例支撑；「感觉变好了」不构成合并依据。

### III. 工具健壮性是中间件（Robustness as Middleware）

- 权限、参数校验、并行、超时、重试、幂等、异常恢复 MUST 在执行引擎（Executor）
  统一实现；禁止把健壮性逻辑散落到单个工具实现里。
- 重试 MUST 仅针对可重试错误（网络抖动、超时、5xx）；非幂等工具 MUST NOT 参与重试，
  超时后副作用状态未知时只能查询确认。

### IV. 必收敛且不编造（Bounded Honesty, NON-NEGOTIABLE）

- Agent 循环 MUST 具备三重收敛保险：`max_steps`、熔断超时（20s 触发降级输出，
  非延迟承诺值）、token 预算（含 reasoning tokens）；任何输入 MUST 在有限步内停止。
- 回答 MUST 只依据检索证据生成；证据不足时 MUST 显式输出「资料不足」并给出改进建议，
  禁止编造条款内容。

### V. 安全与合规是一等公民（Security & Compliance First）

- 租户身份 MUST 只来自 JWT claims（tenant_id / scopes / roles）；
  请求体 MUST NOT 接收 tenant_id。
- `scope`（工具权限域）与 `visibility`（文档密级）MUST 严格分离；
  所有数据访问 MUST 经 DAO 强制注入租户过滤，检索 MUST 同时过滤密级。
- 文档数据模型 MUST 内嵌合规三要素 visibility / region / expire_at；
  删除 MUST 软删 → 物理删两阶段，并写入 append-only + hash chain 的审计日志。

### VI. Contract 解耦、可演进优于一次性正确（Evolvable over One-shot Correct）

- 模块间 MUST 通过 Contract（JSON Schema / Pydantic）解耦；
  模型、向量库、LLM 的替换 MUST NOT 破坏上层接口。
- 每个难以逆转且存在真实取舍的选型 MUST 记录为 ADR（现 ADR-001 ~ ADR-009）。

## 技术栈与事实源约束（Additional Constraints）

- **技术栈**：Python 3.12 · LangGraph · FastAPI；PostgreSQL（pgvector +
  tsvector/BM25，ADR-008）；百炼 qwen3.7-flash / qwen3.7-max +
  qwen3.7-text-embedding / qwen3.7-text-rerank；Redis · MinIO · Langfuse；
  开发期单机 Docker Compose。更换组件 MUST 先立 ADR，再过评测门禁（原则 II）。
- **事实源**：`docs/` 为文档唯一事实源；`data/`（入口 `data/README.md`）为数据事实源，
  只读、不得手工改动；根目录合并文档为脚本生成物
  （`python3 scripts/build_combined_doc.py`），禁止手改。
- **术语**：以 `CONTEXT.md` 与 docs/01 §1.6 术语表为准（scope / visibility /
  evidence / citations / 回归评测 re-run 等）；新文档 MUST 沿用既有术语，
  不得引入同义新词。
- **极简与手术式改动**：只写刚好解决问题的代码；每行改动 MUST 能对应到具体需求，
  禁止顺手重构无关代码。

## 开发与评测工作流（Development Workflow）

- 按里程碑阶段推进（docs/09：阶段 0 已完成，当前阶段 1，总预算 8~12 周）；
  各阶段验收清单是合入依据。
- 影响 RAG 链路的功能 MUST 先定义评测口径（用哪个评测集、哪项指标、基线多少），
  再开始实现。
- 文档变更后 MUST 重建合并文件；提交前 MUST 通过一致性检查
  （grep 断言 + mermaid 结构检查，见 docs 修订流程）。
- 每个 PR MUST 填写 plan 模板的 Constitution Check 门禁；
  违反原则的项必须在 Complexity Tracking 中给出理由与更简替代的否证。

## Governance

- 本章程优先于其他一切开发实践；与任何文档、代码注释冲突时，以本章程为准。
- 修订 MUST 记录修订内容、理由与迁移方案，并按语义化版本递增：
  MAJOR 级 = 原则删除或重定义；MINOR 级 = 新增原则或实质性扩展；PATCH 级 = 澄清与措辞。
- 合规审查：所有 PR / 评审 MUST 验证与章程一致；复杂度 MUST 给出正当理由；
  运行时开发指引见 `CONTEXT.md` 与 `docs/`。

**Version**: 1.0.0 | **Ratified**: 2026-09-02 | **Last Amended**: 2026-09-02
