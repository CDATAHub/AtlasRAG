# Phase 0 Research: AgentLoop

> docs/02·03·04·08 已完成主体设计（ADR 既定），本文件把阶段 2 的实现决策落为
> Decision / Rationale / Alternatives，并消解 plan 全部未知项（LangGraph 集成细节
> 经官方文档查证，2026-09-04）。未新增 NEEDS CLARIFICATION。

## D1. 编排框架与图结构

- **Decision**: LangGraph `StateGraph`（langgraph ≥ 1.0），六节点：
  `plan → route → tool_node → generate → reflect → converge`；
  `tool_node → route` 回环、`reflect` 条件边回 `plan`（重规划）或 `route`（续计划）。
  State 按 docs/03 §3.3：`messages` 用 `add_messages` reducer、`tool_results` 用
  `operator.add`、`plan/current_step/route/evidence/draft/steps/budget` 常规覆盖写。
- **Rationale**: 章程技术栈既定；docs/03 §3.8 骨架即此结构；reducer 语义天然支持
  检查点续跑（追加型字段不重写）。
- **Alternatives**: 手写 async 循环（检查点、恢复、条件边全要自制，成本高）；
  LlamaIndex AgentWorkflow（偏离章程栈）。

## D2. 检查点持久化（FR-018 / US5）

- **Decision**: `AsyncPostgresSaver`（`langgraph-checkpoint-postgres`，基于 psycopg3）
  经 `from_conn_string(DATABASE_URL)` 在 FastAPI lifespan 中进入长生命周期上下文，
  启动时 `await checkpointer.setup()` 建表（checkpoints / checkpoint_blobs /
  checkpoint_writes / checkpoint_migrations，框架自管 schema，本项目不读写）。
  **thread_id = `"{session_id}:{client_msg_id}"`**（每条消息一次图执行）。
  续跑 = 同 config 以 `None` 为输入重新 astream（官方恢复语义）。
- **Rationale**: 官方 Postgres 检查点器，零自研序列化/迁移代码即可满足「进程重启
  续跑」验收；psycopg3 与现有 SQLAlchemy(asyncpg) 并存连同一库，均为小连接池，
  实践常见。
- **Alternatives**: 自定义 `BaseCheckpointSaver` 跑在 asyncpg 上（需自实现 serde 与
  迁移，纯维护负担）；MemorySaver（重启丢失，违反 FR-018）；RedisSaver（多一组件，
  与审计/持久语义不符）。

## D3. 节点内流式事件（FR-015 / US1）

- **Decision**: 节点用 `get_stream_writer()`（或 `writer: StreamWriter` 入参）发出
  统一事件 dict（`{"type": "plan"|"tool_call"|"evidence"|"answer_delta"|...}`）；
  generate 节点把 `LlmClient.stream_chat` 的逐 delta 转为 `answer_delta` 事件；
  API 层以 `graph.astream(..., stream_mode="custom")` 消费并直写 SSE。
- **Rationale**: 官方推荐的自定义数据流通道，与自研 `LlmClient` Protocol（非
  LangChain 模型）完全兼容；事件即契约，prototype 端已按同类事件序实现。
- **Alternatives**: `astream_events` v2（依赖 LangChain 回调体系，与自研客户端错位）；
  generate 完成后一次性返回（丢失流式体验，违反 US1/FR-015）。

## D4. plan/reflect 结构化输出与 token 计量（FR-001/005/007）

- **Decision**: `LlmClient` Protocol 扩展非流式方法
  `chat(messages, *, response_format) -> LlmResult`（`content` + `usage.tokens`），
  生产实现透传 DashScope OpenAI 兼容 `response_format={"type":"json_object"}`；
  `PlanResult` / `ReflectResult` 以 Pydantic 校验，解析失败重试 1 次后走降级路径
  （spec Edge：单步检索 + 生成保底）。`usage.tokens` 逐次累加进 `budget`，作为
  预算保险与「计费」口径（spec FR-013 clarify）。
- **Rationale**: docs/03 §3.4.1 要求 structured output 约束；token 用量必须来自
  服务端 usage 回执而非本地估算，否则预算保险失真。
- **Alternatives**: `with_structured_output`（需 LangChain chat model 封装，与
  自研 Protocol 冲突）；正则抽取 JSON（对引号/换行脆弱）。

## D5. 工具契约与模型可见工具面（章程 I / clarify Q1）

- **Decision**: 新增 `src/tools/`：`Tool` Protocol（name / description / 输入输出
  Pydantic 模型 / `scope` 声明）+ `Registry`（名称→工具，模型可见清单由 scope 过滤）。
  本阶段注册实现 `hybrid_search`（包装阶段 1 的 `rag/hybrid.py` + `rag/rerank.py`
  纯函数，签名不变）；`doc_reader` 仅定义 Pydantic 契约与错误语义，不注册实现。
  模型可见工具清单 = 已注册实现，仅 `hybrid_search` 一项。
- **Rationale**: FR-002 收敛要求 + clarify Q1（仅契约占位）；阶段 1 检索函数
  「纯函数签名」的设计承诺在此兑现（T019）。
- **Alternatives**: 直接在图节点里调检索函数（无契约层，阶段 3 Registry 无法平滑
  替换）；本阶段实现 doc_reader（演示价值低，Q1 已否决）。

## D6. 会话串行化与幂等（FR-012/013）

- **Decision**: 串行化 = 进程内 `asyncio.Lock`（按 session_id 的字典）为前台闸门，
  同会话并发请求立即 `409`；DB 侧 `session.status ∈ idle | running | interrupted`
  作为重启后的真值：启动时把遗留 `running` 复位为 `interrupted`（续跑判定依据）。
  幂等 = `message(tenant_id, session_id, client_msg_id)` 唯一约束：重复提交时——
  该消息已完成 → 直接重放既有事件流（answer 全文 + citations + done）；
  该消息中断 → 触发检查点续跑（clarify Q2）。
- **Rationale**: 单进程演示（D10 同理）下进程锁最简且零竞态；DB 状态保证重启后
  语义正确；唯一约束把幂等判定下沉到存储层，避免先查后插的竞态。
- **Alternatives**: PG advisory lock（跨进程能力，多副本时才需要）；仅应用层查重
  （并发窗口内可重复生成）。

## D7. 上下文管理与压缩（FR-011/014 / US4）

- **Decision**: 进入 plan 前组装上下文：滑动窗口保留最近 6 轮完整消息；历史总长
  超过 3000 token 时把窗口外旧轮次压缩为一条摘要消息（一次 LLM 摘要调用）；
  **已引用的证据与结论以结构化字段（citations 摘录）随摘要保留，永不压缩**
  （docs/03 §3.6「只压对话、不压证据链」）。token 长度用「字符数 ÷ 1.5」近似中文
  开销，仅用于触发判断（不用于预算记账——预算走 D4 的 usage 回执）。
- **Rationale**: 忠实度优先的既定取舍；触发判断无需精确 tokenizer，避免引入
  词表依赖。
- **Alternatives**: tiktoken（非 qwen 词表，仅近似且多一依赖）；不压缩（长会话
  token 成本与延迟随轮数线性膨胀，违反 SC-004）。

## D8. 寒暄快路径与直答路径（FR-003 / SC-005）

- **Decision**: 两条「不检索」路径分开——
  ① **寒暄**（问候/致谢/身份询问，规则表匹配，含长度上限）：API 层短路，模板
  回应，不进图、零 LLM 调用（SC-005 首字节 <1s 的口径即此路径）；
  ② **常识直答**（保险通识类，规则不可判定时由 plan 的 route 判定）：进图但
  route=answer，无工具调用（US1 场景 4 的口径），生成走正常流式。
- **Rationale**: docs/02 §2.5 NFR 明确「寒暄走规则 fast-path 不进 LLM」；
  常识类需真实回答，模板无法覆盖，交由 plan 路由。
- **Alternatives**: 全部规则模板（常识类答非所问）；全部进图（寒暄首字节无法
  <1s，多付一次规划调用）。

## D9. 收敛保险与降级（FR-006/007/008 / US3）

- **Decision**: 三重保险实现：`steps >= max_steps(6)` 硬规则在 route/reflect 判定；
  token 预算（8000）在每次 LLM 调用后以 usage 累加判定，超限即拒发下一次调用；
  整链熔断（20s）用 `asyncio.timeout` 包住图执行，超时发降级 done 事件
  （复用阶段 1 行为，收敛原因 `timeout`）。`convergence_reason ∈ natural |
  max_steps | timeout | budget | refused` 随 done 事件与 runtime_log 落库。
  回环计数 `plan_rounds ≤ 3`（含首轮）。
- **Rationale**: docs/03 §3.5 既定默认值；「任何输入必停」要求三保险相互独立、
  判定点确定（硬规则先行，LLM 判据仅在余量内生效）。
- **Alternatives**: 仅依赖 LLM 反思判断收敛（不确定性，违反章程 IV）。

## D10. 多轮指代消解（FR-011 / US4）

- **Decision**: 不设独立的「指代改写」节点——会话历史（按 D7 组装）直接注入 plan
  与 generate 的 prompt（plan 同时拿到「当前问题 + 近期对话」，输出检索式时隐式
  完成指代消解），证据池跨轮不保留、每条消息独立检索。
- **Rationale**: docs/03 §3.4.1 plan 输入即含 messages；独立改写节点多一次 LLM
  调用且与 plan 职责重叠；「它的宽限期呢」类省略由 plan 产出的改写检索式解决
  （US4 场景 1 可据此验收）。
- **Alternatives**: 独立 query-rewrite 节点（延迟 +1 次 LLM 往返）；跨轮保留
  evidence 池（证据与当前问题错配风险，且与「每问独立可溯」冲突）。

## D11. 评测扩展（SC-001/002 /章程 II）

- **Decision**: `scripts/run_retrieval_eval.py` 增加 `--loop` 模式：同一配置下先跑
  首轮检索（无 LLM，量全量）统计失败集 → 仅对失败集开启回环（plan/reflect 真调
  LLM，规模 = 失败集条数，通常几十条）重跑 → 报告新增 `repair_rate`（修复数 ÷
  首轮失败数）与修复案例清单。SC-001 以首轮指标对比阶段 1 基线（±2σ），SC-002
  看 repair_rate ≥ 0.3。
- **Rationale**: clarify Q4 口径（同配置首轮失败集为分母）；LLM 调用被失败集
  规模约束，成本可控；首轮指标保持「无 LLM、可进 CI」的既有性质。
- **Alternatives**: 全量开回环（全量 LLM 调用，贵且慢，违反 D9 预算精神）；
  以阶段 1 旧报告为分母（配置不一致，clarify 已否决）。

## D12. SSE 事件契约扩展（FR-015 / docs/08 §8.2）

- **Decision**: 事件序扩为 `plan → tool_call* → evidence → answer* → citations →
  done`（多轮回环时 plan/tool_call/evidence 可重复出现，answer 之前的最后一个
  evidence 为准）；`done` 扩展 `session_id / message_id / convergence_reason /
  rounds / steps / tokens_used`。错误码新增 `409`（会话并发）。阶段 1 的
  `evidence → answer → citations → done` 事件字段全部保留不动（向后兼容）。
- **Rationale**: prototype 已按 docs/08 §8.2 模拟 plan/tool_call 事件，接入即用；
  阶段 1 契约测试不破坏。
- **Alternatives**: 独立 v2 端点（两套契约维护）；事件内嵌套轮次结构（客户端
  解析复杂化）。
