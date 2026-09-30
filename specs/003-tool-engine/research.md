# Phase 0 Research: Tool 层生产化

> 决策 D1~D9，均回指 spec FR/US/SC 与 clarify。现状基线：`src/tools/base.py`
> （Tool Protocol + Registry + ToolContext）、`tool_node` 直接 invoke、
> `plan.py` 硬编码 `visible_tools(["retrieval:read"])`、`build_tool_registry`
> 仅注册 hybrid_search、检索 SQL 仅租户过滤（无 visibility，clarify Q3）。

## D1. ToolPolicy 契约形态（FR-001）

**Decision**: 在既有 `Tool` Protocol 上新增 `ToolPolicy` dataclass 属性：
`timeout_ms / max_retries / idempotent / parallel_safe / required_scopes: tuple[str, ...]`。
工具以类属性 `policy` 声明；未声明时 Registry/Executor 应用保守默认（Settings：
`tool_default_timeout_ms=5000`、`tool_default_max_retries=1`、idempotent=True、
parallel_safe=True、required_scopes 取既有单值 `scope` 字段）。

**Rationale**: 保持 `Tool` Protocol 单入口不变（图与工具实现零迁移成本）；
docs/04 §4.2 的 `ToolContract` 字段全部落位且不引入第二套注册抽象。单值 `scope`
保留为缺省 required_scopes 的 sugar（既有两工具与测试引用不破坏）。

**Alternatives**: ① 独立 `ToolContract` BaseModel 替代 Protocol（docs/04 骨架原样）——
推翻 002 已落地的 Protocol 注册路径，迁移面大无增量收益；② 全部策略放 Settings
全局单值——丢失 per-tool 粒度，docs/04 明确每工具一份契约。

## D2. 执行引擎结构与超时机制（FR-004~008，US1）

**Decision**: 新增 `src/tools/executor.py`：`ToolExecutor.execute(call, ctx) -> ToolExecutionRecord`
按固定顺序实施「权限（required_scopes ⊆ ctx.scopes）→ 参数校验（args_model.model_validate）
→ 幂等判断（idempotent=False → 禁止重试）→ 执行 → 超时（asyncio.wait_for）→ 重试
（可重试错误指数退避）→ 异常恢复（统一包装 ToolExecutionError）」；
`execute_step(calls, ctx)` 负责一步多调用的并行编排（parallel_safe 判定，FR-011）。
超时/重试常量与默认策略进 Settings。

**Rationale**: 中间件以单一 execute 函数的固定阶段序列实现（顺序即章程 III 管道），
而非层层装饰器——阶段内只有一条调用路径，函数内分阶段最简且测试可直接注入各阶段故障。
超时用 `asyncio.wait_for`：002 的「生产者任务 + 截止时间队列」教训针对 ASGI 任务组
（task.cancel 穿透 anyio 取消整个请求）；executor 运行在 LangGraph 图节点内部、
不在 ASGI 任务组中，wait_for 的取消仅向上传播到当前 await 点，安全。

**Alternatives**: ① 中间件链（每能力一个可组合 wrapper）——v1 无组合需求，五层闭包
增加理解成本；② 沿用生产者任务模式——为不存在的穿透问题付出复杂度。

## D3. 可重试错误分类与退避（FR-007/008，SC-003/004）

**Decision**: `ToolError` 扩展子类 `ToolTimeoutError`（可重试）；executor 以
`error_class ∈ {validation, permission, timeout, transient, permanent, unknown}`
分类。可重试 = timeout / transient（工具实现以 `ToolTransientError` 标注瞬时故障，
如网络/5xx）；不可重试 = validation / permission / permanent。退避：
`base 0.2s × 2^n`（n=尝试序），max_retries 耗尽即失败。非幂等工具（idempotent=False）
在任何失败下 attempts=1，零重发（FR-008）。

**Rationale**: docs/04 §4.3.5 的 is_retryable 判据落地；hybrid_search 现有
「任意异常 → ToolError」包装保持不变（统一进 unknown 类，不重试），后续如需
标注 embedding/rerank 瞬时故障再细化——本阶段不预支。

**Alternatives**: 按 HTTP 状态码自动分类——工具层已统一 ToolError，重复分类无据。

## D4. scopes 注入链路（FR-003，US2）

**Decision**: AgentState 增输入字段 `scopes: list[str]`；chat 路由从 JWT claims
`scopes` 解析注入（ADR-009 既有 pyjwt 基础），缺省/缺字段回退 `["retrieval:read"]`
（与阶段 2 行为向后兼容）。plan 节点工具面改用 `registry.visible_tools(state["scopes"])`；
executor 执行前再校验（模型幻觉出的未授权工具名 → permission 拒绝，不执行实现）。

**Rationale**: spec Assumptions「API 层注入、JWT 完整落地留阶段 5」的最小实现；
执行前二次校验是 US2 验收场景 2 的落点。TokenContext（ctx）增 `scopes` 字段。

**Alternatives**: 仅在 executor 校验、plan 面维持硬编码——模型会规划出不可调用的
工具，违反 FR-003 源头收敛。

## D5. 计划步多调用扩展（FR-011，clarify Q1）

**Decision**: `PlanStep` 增可选字段 `calls: list[dict] | None`（每项
`{"tool","query","rationale"}`）；缺省 None 走既有单调用路径（step.tool/query）。
tool_node：step.calls 存在且 >1 时经 `executor.execute_step` 并行执行（全 parallel_safe
时 gather；含非 parallel_safe 则整体串行保序），事件 `tool_call` 每调用一条（同 step）。
SYSTEM_PLANNER 增多调用措辞：独立子问题可合并进同一步的 calls（上限 4）。

**Rationale**: 旧格式 plan dict（含检查点内已持久化的）不带 calls 键——可选字段
保证续跑/重放兼容；并行编排全部下沉 executor（tool_node 仅传整步）。

**Alternatives**: 平铺 calls 为独立 step——丢失「同一步独立子问题」语义，
reflect 重规划前缀保留逻辑（FR-005）要重写切分。

## D6. doc_reader 实现（FR-012，US3）

**Decision**: 查 `chunk where doc_id=? and tenant_id=? and chunk_type='parent'`
（+ `sec_no=?` 指定章节），按 sec_no 排序组装
`sections=[{sec_no,title,text}]`（title 取 chunk.meta['title']，缺失回退文档 title
+ sec_no 拼接）；跨租户/不存在 → 空结果按 not_found 结构化错误；sec_no 不存在 →
`section_not_found` 错误（不回退全文）。Document 表取 title（租户过滤）。

**Rationale**: 契约（002 先行定义的 DocReaderArgs/Result）不变；「父块=条款语义单元」
与 hybrid_search 证据口径一致。密级过滤按 clarify Q3 推迟阶段 5。

**Alternatives**: 读 child 块全量——粒度过碎撑爆上下文；全文拼接——
doc_reader(sec_no=None) 即分章返回，等价且可定位。

## D7. MCP 接入（FR-002/013/014/015，US5）

**Decision**: 新增依赖 `mcp`（官方 Python SDK）；`src/tools/mcp_client.py` 用
stdio ClientSession 连本地 `scripts/mcp_demo_server.py`（FastMCP：保费试算/等待期
计算两个演示工具），`list_tools` 的 inputSchema 自动转 Pydantic 动态 args_model 注册
（name 加 `mcp.` 前缀，required_scopes 取配置 `mcp_tool_scopes`，缺省 `["misc:calc"]`）；
Server 不可达/清单非法 → 该来源工具不注册 + warning 日志（FR-014），应用启动不失败。
测试用 SDK 的 in-memory transport pair（无子进程、无真实 IO，章程 VII）。
连接生命周期挂 FastAPI lifespan（startup 连接/注册，shutdown 关闭）。

**Rationale**: stdio 最简可演示（JD「接入 MCP Server」）；动态契约满足 FR-013
「不手工维护两份定义」；scope 配置化演示「按场景加入工具面」（docs/04 §4.5）。

**Alternatives**: ① streamable-http transport——需要起端口/健康检查，演示价值相同
成本更高；② 不接 SDK 手写 JSON-RPC——重复造轮子。

## D8. 执行记录与 SSE 事件扩展（FR-010，SC-008；契约 v2.1）

**Decision**: `ToolExecutionRecord`（内存契约）字段：
`tool / query / step / ok / hits / top_score / error / error_class / attempts /
duration_ms / trace_id`；tool_results/evidence entry 即其序列化形态。
SSE `tool_call` 事件增可选 `attempts`、`duration_ms`；失败时同 step 的 `evidence`
事件维持 `failed:true`（002 契约不变，仅加字段）。runtime_log **零改动**
（聚合指标 steps/tokens_used 已覆盖；调用级明细走 tool_results + Langfuse 阶段 5）。

**Rationale**: 最小可观测满足 FR-010 携带项（tool_name/分类/重试/耗时/trace_id）；
SSE 只加可选字段，prototype/index.html 与既有消费端零破坏。

**Alternatives**: runtime_log 加列或新建 tool_call_log 表——调用级持久化是阶段 5
Langfuse Tracing 的范围，预支且稀释本阶段焦点。

## D9. 测试与回归策略（SC-001~008，章程 VII）

**Decision**: ① FakeTool：可编程故障（挂起 N 秒/前 k 次抛 ToolTransientError/
抛 ValidationError/非幂等标注），驱动 executor 单测逐中间件断言；② 既有 100 项测试
零修改全绿 = SC-001 mock 口径（executor 对 hybrid_search 是透明包装：同 args 同结果，
单测断言透传语义）；③ 并行集成测：两 FakeTool 各 sleep 0.2s，断言墙钟 <0.3s 且结果
保序（SC-006）；④ MCP：in-memory transport 断言清单→注册→调用→降级四路径；
⑤ demo_tool_engine.py 零 LLM 端到端脚本（quickstart 验证物，演示管道全行为）。

**Rationale**: 全部验收不依赖真实外部服务；SC-001 真实检索层 bit 级对比与端到端
评测按 clarify Q2 合并进 002-T045 债务批次。

**Alternatives**: CI 起 MCP 子进程做集成——in-memory transport 已覆盖同一 SDK 代码
路径，子进程只引入 flaky。
