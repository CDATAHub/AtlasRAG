# Phase 1 Data Model: Tool 层生产化

> 本阶段**无新表、无库表变更**。数据模型层的产出是三组内存契约实体
> （Pydantic/dataclass，research D1/D2/D8）、AgentState 输入扩展与
> 计划步 schema 的检查点兼容约定。库表（document/chunk/session/message/
> runtime_log）沿用阶段 1/2 定义零改动。

## 实体

### ToolPolicy（工具执行策略，FR-001）

工具类属性声明的执行策略元数据；未声明时由 executor 应用 Settings 保守默认。

| 字段 | 类型 | 缺省（Settings） | 说明 |
|---|---|---|---|
| timeout_ms | int | tool_default_timeout_ms=5000 | 单次尝试的执行上限；超时 → ToolTimeoutError |
| max_retries | int | tool_default_max_retries=1 | 可重试错误的额外尝试次数上限（总尝试 = 1+max_retries） |
| idempotent | bool | True | False 时任何失败零重发（FR-008） |
| parallel_safe | bool | True | False 时同一步内串行保序（FR-011） |
| required_scopes | tuple[str, ...] | 取工具单值 scope | 权限域集合；可见/可调 ⇔ 完全被请求 scopes 覆盖（FR-003） |

内置工具声明值：

| 工具 | required_scopes | timeout_ms | max_retries | idempotent | parallel_safe |
|---|---|---|---|---|---|
| hybrid_search | ("retrieval:read",) | 8000 | 1 | True | True |
| doc_reader | ("retrieval:read",) | 5000 | 1 | True | True |
| mcp.*（动态） | 取配置 mcp_tool_scopes | 5000 | 1 | True | True |

### ToolExecutionRecord（执行记录，FR-010）

一次工具调用的结果载体；序列化后即 tool_results/evidence entry（002 既有字段
ok/error/hits/top_score 全保留，新增字段可选）。

| 字段 | 类型 | 说明 |
|---|---|---|
| tool / query / step | str / str / int | 002 既有 |
| ok | bool | 成功/失败 |
| hits / top_score | list[dict] / float\|None | 成功时的证据载荷（doc_reader 为 sections 摘要） |
| error / error_class | str / 枚举 | 失败消息与分类：validation \| permission \| timeout \| transient \| permanent \| unknown |
| attempts | int | 实际尝试次数（≥1；SC-004 审计口径：非幂等失败恒为 1） |
| duration_ms | int | 含全部重试的端到端耗时 |
| trace_id | str | 贯穿请求的追踪标识 |

不变式：ok=True ⇒ error/error_class 为空；error_class=timeout/transient 且
attempts>1 ⇒ idempotent=True（非幂等零重发）。

### ToolCallSpec（计划步内单调用，FR-011 / clarify Q1）

`{"tool": str, "query": str, "rationale": str=""}` —— PlanStep 新增可选字段
`calls: list[ToolCallSpec] | None` 的元素形态。

## AgentState 扩展（research D4）

| 新增字段 | 类型 | 注入点 | 说明 |
|---|---|---|---|
| scopes | list[str] | chat 路由（JWT claims.scopes，缺省 ["retrieval:read"]） | 驱动 plan 工具面与 executor 权限校验 |

既有字段零改动；plan/tool_results/evidence 等 reducer 语义不变。

## 检查点兼容（clarify Q1 的持久化约束）

LangGraph 检查点（thread `{session_id}:{client_msg_id}`）内已持久化的旧格式
plan dict **不含 `calls` 键**：

- 读取侧：tool_node 以 `step.get("calls")` 判定——缺键走既有单调用路径，
  续跑/幂等重放对旧检查点行为不变
- 写入侧：新格式仅在模型输出多调用时携带 `calls`；单调用输出仍写
  `tool/query` 字段（不强制双写）

## 关系

```text
JWT claims ──scopes──▶ AgentState.scopes ──▶ plan 节点（visible_tools 收敛）
                                          └─▶ executor（执行前二次校验）
PlanStep.calls[] ──▶ executor.execute_step ──▶ ToolExecutionRecord[]
                                                  ├─▶ state.tool_results / evidence（append-only reducer）
                                                  └─▶ SSE tool_call 事件（attempts/duration_ms 可选字段）
MCP Server ──list_tools──▶ 动态 args_model + ToolPolicy ──▶ Registry（mcp. 前缀隔离）
```

## 校验规则（来自 spec FR）

- FR-003：required_scopes ⊆ 请求 scopes ⇔ 可见（visible_tools）且可调（executor 权限校验）
- FR-005：args_model 校验失败 → error_class=validation，attempts=1，不进入工具实现
- FR-007/008：error_class ∈ {timeout, transient} 才可重试；idempotent=False ⇒ attempts 恒 1
- FR-011：step.calls 全 parallel_safe ⇒ 并行（gather 保序合并）；否则整步串行
- FR-012：doc_reader 跨租户/不存在 → not_found；sec_no 不存在 → section_not_found（不回退全文）
