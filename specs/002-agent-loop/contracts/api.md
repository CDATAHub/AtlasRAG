# Phase 1 Contracts: 会话化问答 + 会话管理接口

> 在阶段 1 [contracts/api.md](../001-single-chain-rag/contracts/api.md) 基础上扩展；
> 文档上传/状态/重处理接口不变。鉴权不变（`Authorization: Bearer <JWT>`，HS256，
> claims: tenant_id/scopes/exp）。错误响应统一 `{"code","message","trace_id"}`，
> 新增 `409 conflict`。事件序对齐 docs/08 §8.2 与 `prototype/index.html` 已模拟的
> 形态（research D12）。

## 1. POST /v1/chat — 问答（SSE，v2）

请求：`application/json`

```json
{ "question": "这款重疾险等待期多久？等待期内出险赔吗？",
  "session_id": "sess-7f3a…",      // 可选，缺省新建会话
  "client_msg_id": "c-101" }       // 可选幂等键，缺省服务端生成
```

- `question` 必填，非空，≤500 字符（422）；`session_id` 不存在/已删/跨租户 → 404
- 同会话已有进行中请求 → `409 {"code":"session_busy","message":"上一条回答仍在进行中"}`
- 幂等重放（同 tenant+session+client_msg_id 已存在）：
  - 该消息已完成 → `200` 重放既有事件流（`answer` 全文单 delta + `citations` + `done`，
    `replayed: true`），不重复生成（FR-013）
  - 该消息执行中曾中断 → `200` 触发检查点续跑，正常流式输出剩余部分（clarify Q2）

响应：`200`，`Content-Type: text/event-stream`，事件序固定：

```
event: plan
data: {"round":1,"session_id":"sess-7f3a…","message_id":"msg-01…",
       "steps":[{"step":1,"action":"retrieve","tool":"hybrid_search",
                 "query":"重疾险 等待期 定义 起算","rationale":"…"},
                {"step":2,"action":"retrieve","tool":"hybrid_search",
                 "query":"等待期 内 出险 保险责任","rationale":"…"}]}

event: tool_call
data: {"step":1,"tool":"hybrid_search","query":"重疾险 等待期 定义 起算"}

event: evidence
data: {"round":1,"trace_id":"tr-7f3a","hits":[{"n":1,"doc_id":"…","sec_no":"2.3.1",
       "title":"康护一生…条款","score":0.92}]}

event: answer
data: {"delta":"等待期为 90 日"}

event: answer
data: {"delta":"，自本合同生效日起算[1]。等待期内出险不承担给付责任[2]。"}

event: citations
data: {"citations":[{"n":1,"doc_id":"…","sec_no":"2.3.1","title":"…",
       "quote":"自本合同生效（或最后复效）之日起 90 日内为等待期。","score":0.92}]}

event: done
data: {"trace_id":"tr-7f3a","session_id":"sess-7f3a…","message_id":"msg-01…",
       "client_msg_id":"c-101","latency_ms":4120,"refused":false,
       "hit_count":3,"top_score":0.92,"convergence_reason":"natural",
       "rounds":1,"steps":2,"tokens_used":1832}
```

事件语义：

- 完整序：`(plan → tool_call* → evidence)* → answer* → citations → done`；
  回环轮次时 `plan/tool_call/evidence` 重复出现，`round` 递增（≤3），以最后一个
  `evidence` 为最终证据（FR-006/015）
- `plan` 事件在直答路径（route=answer）可省略 tool 步骤或整事件不存在——仅寒暄
  快路径完全不进图（见 §4）；常识直答有 `plan`（route=answer、steps 空）
- `answer` 中 `[n]` 与 `citations[].n` 一一对应；`quote` 必须为条款原文子串（阶段 1
  FR-006/007 口径不变）
- `done.convergence_reason ∈ natural | max_steps | timeout | budget | refused`，
  与 runtime_log 落库值一致（FR-015/016）

寒暄快路径（research D8，模板直答，零 LLM/检索）：

```
event: answer
data: {"delta":"您好！我是保险条款问答助手，请描述您想了解的条款问题，例如「等待期是多久」。"}

event: citations
data: {"citations":[]}

event: done
data: {"trace_id":"tr-7f3c","session_id":"…","message_id":"…","latency_ms":40,
       "refused":false,"hit_count":0,"top_score":null,
       "convergence_reason":"natural","rounds":0,"steps":0,"tokens_used":0}
```

拒答/降级路径：沿用阶段 1 契约（单 delta + 空 citations + `refused=true`），
`convergence_reason=refused`；熔断超时（20s）降级输出 `convergence_reason=timeout`，
预算耗尽 `convergence_reason=budget`，步数上限 `convergence_reason=max_steps`
（FR-007/008）。

错误：`401` / `422` / `503` 同阶段 1；新增 `409 session_busy`（FR-012）。

## 2. GET /v1/sessions/{session_id} — 会话历史

响应 `200`：

```json
{ "session_id": "sess-7f3a…", "title": "这款重疾险等待期多久？", "created_at": "…",
  "messages": [
    { "message_id": "msg-01…", "role": "user",
      "content": "这款重疾险等待期多久？", "created_at": "…" },
    { "message_id": "msg-02…", "role": "assistant",
      "content": "等待期为 90 日……", "citations": [ { "n": 1, "…": "…" } ],
      "trace_id": "tr-7f3a", "created_at": "…" } ] }
```

- 按时间有序，含全部问答与引用（FR-010 / US4 场景 2）
- 不存在 / 已删除 / 跨租户 → `404`（不泄露存在性，FR-017）

## 3. DELETE /v1/sessions/{session_id} — 删除会话

响应 `204`；软删（`deleted_at` 置位，FR-010 / clarify Q5）。删除后：

- 历史 → 404；续跑与幂等重放 → 404；跨租户删除他人会话 → 404
- 有进行中请求时删除 → 该请求完成后删除生效（data-model 状态机；US4 边界）

## 4. 快路径与图路径判定（research D8）

| 输入形态 | 判定 | 行为 |
|---|---|---|
| 问候/致谢/身份询问（规则表，≤30 字符） | 寒暄 | API 层短路，模板回应，零 LLM/检索（SC-005 口径） |
| 条款/产品问题 | 检索 | 完整图路径 |
| 保险通识、无需检索 | plan route=answer | 进图，无 tool_call，LLM 流式直答 |
| 条款库为空 / 检索不可用 | — | `503`（阶段 1 FR-010 口径） |

## 契约 ↔ mock 对应（章程 VII）

| 契约元素 | Fake 实现（tests/） | 说明 |
|---|---|---|
| plan/reflect 结构化输出 | `FakeLLM` 脚本化：按消息特征返回预置 PlanResult/ReflectResult JSON | 契约为 Pydantic 模型，fake 与真实实现同源校验 |
| 生成增量 delta | `FakeLLM` 按脚本吐 delta（阶段 1 既有） | usage 回执可编程（预算边界用例） |
| 工具返回 | `FakeRerank/FakeEmbedding` + 播种库（阶段 1 既有） | hybrid_search 包装不变 |
| 检查点 | `AsyncPostgresSaver` 真实实例（本地 PG，非外部服务） | 章程 VII：PG 是测试基座，非外部服务 |
| LLM 解析失败 | `FakeLLM` 返回非法 JSON 一次后恢复正常 | 验证降级路径（spec Edge） |
