# Phase 1 Data Model: AgentLoop（新增会话/消息，扩展运行档案）

> 新增 session / message 两表，扩展 runtime_log；document / chunk 不变
> （见 [specs/001-single-chain-rag/data-model.md](../001-single-chain-rag/data-model.md)）。
> 字段与 docs/07 §7.7、docs/08 §8.3 对齐；合规字段预留口径同阶段 1（章程 V），
> 会话过期字段仅预留（clarify Q5）。

## 实体

### session（会话，新增）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID PK | | 会话标识（客户端可见 `sess-` 前缀形式） |
| tenant_id | TEXT NOT NULL | JWT claims 注入 | 租户隔离；`BTREE (tenant_id, id)` |
| status | TEXT NOT NULL DEFAULT 'idle' | CHECK in (idle, running, interrupted) | 串行化真值：running=有进行中请求；进程启动时 running→interrupted（research D6） |
| title | TEXT NULL | | 首条问题截断，列表展示用 |
| expire_at | TIMESTAMPTZ NULL | | 过期时间（本阶段仅预留字段，清理逻辑阶段 5，clarify Q5） |
| deleted_at | TIMESTAMPTZ NULL | | 软删标记；非 NULL 即不可查询/续跑 |
| created_at / updated_at | TIMESTAMPTZ DEFAULT now() | | |

**状态机**：`idle → running`（请求进入）→ `idle`（完成/失败/降级）；
`running → interrupted`（仅进程启动复位，遗留运行标记）。
所有查询与续跑判定强制 `deleted_at IS NULL AND tenant_id = :ctx`。

### message（会话消息，新增）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID PK | | 消息标识（客户端可见 `msg-` 前缀形式） |
| session_id | UUID FK → session ON DELETE CASCADE | | |
| tenant_id | TEXT NOT NULL | | 冗余，查询免 join |
| client_msg_id | TEXT NOT NULL | | 客户端幂等键；缺省时服务端生成 |
| role | TEXT NOT NULL | CHECK in (user, assistant) | 提问/回答 |
| content | TEXT NOT NULL | | 消息文本（回答为最终全文） |
| citations | JSONB NOT NULL DEFAULT '[]' | | 回答的引用列表（阶段 1 citations 结构） |
| trace_id | TEXT NULL | | 关联 runtime_log（仅 assistant 行） |
| created_at | TIMESTAMPTZ DEFAULT now() | | |

**不变式**：
- `UNIQUE (tenant_id, session_id, client_msg_id)`——幂等判定的存储层保障（FR-013）
- 提问行与回答行成对（一问一答按 created_at 有序）；会话历史 = 该会话全部消息按时间序
- `client_msg_id` 未提供时服务端生成并随 done 事件返回，客户端后续重放可用

### runtime_log（运行档案，扩展列）

阶段 1 既有列全部保留，新增：

| 字段 | 类型 | 说明 |
|---|---|---|
| session_id | UUID NULL | 所属会话（单轮无会话为 NULL） |
| message_id | UUID NULL | 所属消息 |
| client_msg_id | TEXT NULL | 幂等键归档 |
| plan_rounds | INT NOT NULL DEFAULT 1 | 计划轮数（回环 ≤3，FR-006） |
| steps | INT NOT NULL DEFAULT 0 | 已执行步数（保险 1 计数） |
| tokens_used | INT NOT NULL DEFAULT 0 | 本轮 LLM 总用量（usage 回执累加，FR-007/016） |
| convergence_reason | TEXT NOT NULL DEFAULT 'natural' | CHECK in (natural, max_steps, timeout, budget, refused) |

### LangGraph 检查点（框架自管，不建模）

`AsyncPostgresSaver.setup()` 建的 `checkpoints` / `checkpoint_blobs` /
`checkpoint_writes` / `checkpoint_migrations` 四表由框架读写，本项目不做 ORM 映射、
不手工变更；`thread_id = "{session_id}:{client_msg_id}"`（research D2）。

## 关系

```
session 1──N message（级联删除）
message 1──1 runtime_log（逻辑关联 trace_id / message_id，无外键）
session/message 只增不改内容（append-only 对话史；status/deleted_at 除外）
```

## 校验规则（来自 spec FR）

| 规则 | 来源 |
|---|---|
| 重复 (tenant_id, session_id, client_msg_id) → 幂等返回既有结果或触发续跑 | FR-013 / clarify Q2 |
| 同 session 已有 running → 新请求 409 | FR-012 |
| 删除会话 → deleted_at 置位，此后查询/续跑均 404 | FR-010 / US4 场景 6 |
| 跨租户访问 session/message → 404（不泄露存在性） | FR-017 / 章程 V |
| done 事件与 runtime_log 的 convergence_reason 一致 | FR-015/016 / US3 场景 4 |
| plan_rounds ≤ 3、steps ≤ 6、tokens_used ≤ 8000（超限即已收敛，档案可证） | FR-006/007 / SC-003 |
| 压缩后回答引用仍可展开原文（citations 摘录随摘要保留） | FR-014 / SC-007 |
