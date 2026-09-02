# Phase 1 Contracts: 最小 HTTP 服务三接口

> 裁剪自 docs/08 §8.2（clarify Q1）；事件序与 `prototype/index.html` 已实现的模拟一致，
> 后端落地后原型仅改端点地址。鉴权：三接口统一 `Authorization: Bearer <JWT>`
> （HS256，claims: tenant_id/scopes/exp；`scripts/issue_token.py` 签发）。
> 错误响应统一 `{"code": ..., "message": ..., "trace_id": ...}`。

## 1. POST /v1/documents — 上传条款文档

请求：`multipart/form-data`，字段 `file`（.txt / .md；PDF 解析为后续子项，本阶段上传
PDF 返回 415 与提示）。

响应 `202 Accepted`：

```json
{ "doc_id": "0c1d902e-…", "title": "康护一生重大疾病保险条款.txt",
  "version": 1, "status": "processing" }
```

语义：
- 同租户下 `content_hash` 已存在 → `200 OK` 返回既有文档（`"status": "<现状态>"`，
  响应头 `X-Deduplicated: true`），不重复入库（FR-003 / clarify Q2）
- 内容同 title 不同指纹 → 新行，`version = max+1`
- 无法解析 → 仍 202 受理，随后状态转 `failed`（错误在状态接口可见），支持重新触发

## 2. GET /v1/documents/{doc_id}/status — 处理状态

响应 `200`：

```json
{ "doc_id": "0c1d902e-…", "status": "indexed",
  "blocks": { "parents": 42, "children": 128 }, "error": null }
```

- `status ∈ processing | indexed | failed`；`failed` 时 `error` 给出原因，`POST
  /v1/documents/{doc_id}/reprocess` 触发重试（同一接口族，最小实现）
- 他租户的 doc_id → 404（不泄露存在性）

## 3. POST /v1/chat — 问答（SSE）

请求：`application/json`

```json
{ "question": "这款重疾险等待期多久？" }
```

响应：`200`，`Content-Type: text/event-stream`，事件序固定：

```
event: evidence
data: {"trace_id":"tr-7f3a","hits":[{"n":1,"doc_id":"…","sec_no":"2.3.1",
       "title":"康护一生…条款","score":0.92}]}

event: answer
data: {"delta":"等待期为 90 日"}

event: answer
data: {"delta":"，自本合同生效日起算[1]。"}

event: citations
data: {"citations":[{"n":1,"doc_id":"…","sec_no":"2.3.1","title":"…",
       "quote":"自本合同生效（或最后复效）之日起 90 日内为等待期。","score":0.92}]}

event: done
data: {"trace_id":"tr-7f3a","latency_ms":1820,"refused":false,
       "hit_count":2,"top_score":0.92}
```

- `evidence → answer(delta)* → citations → done`，顺序固定（契约测试断言）
- 引用标记 `[n]` 出现在 answer 文本中，与 `citations[].n` 一一对应（FR-006）
- `quote` 必须是条款原文子串，服务端从 chunk.text 定位截取，禁止合成（FR-007）

拒答路径（top_score < 阈值，FR-008）：

```
event: answer
data: {"delta":"未在当前条款库中找到与该问题直接相关的条款。建议补充险种名称或条款术语（如「等待期」「宽限期」）再试。"}

event: citations
data: {"citations":[]}

event: done
data: {"trace_id":"tr-7f3b","latency_ms":940,"refused":true,"hit_count":0,"top_score":0.31}
```

错误：
- `401`：缺失/无效 JWT（`{"code":"unauthorized",…}`）
- `422`：question 为空或超长（>500 字符）
- `503`：检索能力不可用（如索引为空），`{"code":"service_unavailable","message":"条款库暂不可用，请稍后再试"}`（FR-010）
- 链路超时（>20s 熔断）：流内发 `done` 事件（`refused=true`，原因字段 `timeout`），不断连（FR-008 / 章程 IV）

## 契约 ↔ mock 对应（章程 VII）

| 契约元素 | Fake 实现（tests/） | 录制夹具 |
|---|---|---|
| Embedding 返回 1024 维向量 | `FakeEmbedding`（哈希向量，确定性） | fixtures/embedding/*.json |
| Rerank 返回分数列表 | `FakeRerank`（可编程分数） | fixtures/rerank/*.json |
| LLM 增量 delta 序列 | `FakeLLM`（按脚本吐 delta） | fixtures/llm/*.jsonl |
| JWT 校验 | 真实 PyJWT（本地方案，无需 mock） | — |
