# Phase 1 Contracts: SSE 事件契约扩展（v2.1）

> 在阶段 2 [contracts/api.md](../002-agent-loop/contracts/api.md) 基础上扩展。
> **对外 HTTP 接口零变化**：端点、鉴权、错误码、事件序
> `（plan → tool_call* → evidence)* → answer* → citations → done` 全部不变；
> 本阶段仅扩展既有事件的**可选字段**并新增 plan 多调用形态。旧消费端
> （prototype/index.html）零破坏。

## 1. POST /v1/chat — 事件字段扩展（v2.1）

### plan 事件（多调用形态，FR-011 / clarify Q1）

单调用步骤输出不变；模型将独立子问题合并进同一步时，`steps[].calls` 出现：

```
event: plan
data: {"round":1,"session_id":"…","message_id":"…",
       "steps":[
         {"step":1,"action":"retrieve","tool":"hybrid_search",
          "query":"重疾险 等待期 定义",
          "calls":[{"tool":"hybrid_search","query":"重疾险 等待期 定义 起算","rationale":"等待期定义"},
                   {"tool":"doc_reader","query":"doc:9f2c…/sec:2.3","rationale":"精读等待期条款原文"}]}]}
```

- `calls` 为可选字段：缺省即单调用（step.tool/query 语义不变，检查点兼容，
  [data-model.md](../data-model.md)）
- `calls` 上限 4；超出按独立步骤输出
- 单调用字段与 `calls` 同时存在时以 `calls` 为准（tool_node 执行口径）

### tool_call 事件（可观测扩展，FR-010）

```
event: tool_call
data: {"step":1,"tool":"hybrid_search","query":"重疾险 等待期 定义 起算",
       "attempts":2,"duration_ms":812}
```

- `attempts`（实际尝试次数，含重试）、`duration_ms`（端到端含重试）为可选；
  缺省可省略——仅在 >1 次尝试或需要观测时携带
- 一步多调用时每调用一条 `tool_call`（同 `step`，顺序与 calls 一致）
- 失败调用的结构化信息维持既有口径：同 step 的 `evidence` 事件带 `failed:true`，
  `tool_call` 事件本身不携带 error（002 契约不变）

### evidence / answer / citations / done 事件

零变化（002 §1 定义）。失败调用的证据载荷为空 `hits` + `failed:true`，
`error_class` 不外发 SSE（内部口径，见 data-model.md ToolExecutionRecord）。

## 2. 鉴权与 scopes 注入（FR-003，US2）

- JWT claims 扩展读取：`scopes: ["retrieval:read", …]`（数组，缺省回退
  `["retrieval:read"]`，与阶段 2 行为向后兼容）；请求体仍 MUST NOT 接收 tenant_id/scopes（章程 V）
- scopes 仅影响工具可见面与工具调用，不影响消息/会话的租户归属校验（002 §2/§3 不变）
- scopes 不含 `retrieval:read` 的请求：工具面为空 → plan 走既有「无工具可用」
  降级路径（拒答模板，非新错误码）

## 3. MCP 工具的调度口径（FR-002/013/015，US5）

- MCP 工具以 `mcp.<name>` 进入工具面（仅当请求 scopes 覆盖配置的
  `mcp_tool_scopes`，缺省 `["misc:calc"]`），plan/tool_call/evidence 事件
  形态与内置工具完全一致（模型与消费端不感知来源差异）
- MCP 来源故障（Server 不可达/清单非法）：该来源工具不出现在任何请求的工具面，
  请求本身不报错（FR-014）

## 契约 ↔ mock 对应（章程 VII）

| 契约元素 | Fake 实现（tests/） | 说明 |
|---|---|---|
| 执行管道行为（超时/重试/分类） | `FakeTool`：可编程挂起、前 k 次 ToolTransientError、ValidationError、非幂等标注 | 驱动 executor 单测与 SC-002/003/004 断言 |
| 并行多调用 | 两 FakeTool 各 sleep 0.2s，断言墙钟 <0.3s、结果保序 | SC-006 |
| MCP 清单→注册→调用 | `mcp` SDK in-memory transport pair（无子进程/无 IO） | FR-013/014 四路径（正常/不可达/非法 schema/重名隔离） |
| doc_reader 读取 | 播种库（parent 块 + sec_no），跨租户用例 | FR-012 / US3 |
| 既有 100 项回归 | 002 既有 Fake 全部复用，零修改 | SC-001 mock 口径 |
