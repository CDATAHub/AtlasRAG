# Phase 1 Data Model: 单链路 RAG

> 实体取自 spec Key Entities；字段与 docs/07 §7.7 数据模型对齐并裁剪到阶段 1。
> 合规三字段（visibility/region/expire_at）与 version/tenant_id 从第一版建表即预留
> （章程 V），本阶段仅 visibility 参与过滤，region/expire_at 不启用业务逻辑。

## 实体

### document（条款文档）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID PK | | 文档标识 |
| tenant_id | TEXT NOT NULL | JWT claims 注入 | 租户隔离 |
| title | TEXT NOT NULL | | 文档标题（上传文件名或条款名） |
| source | TEXT NOT NULL DEFAULT 'upload' | | 来源（upload / corpus_import） |
| content_hash | TEXT NOT NULL | | SHA-256，同版本判定（clarify Q2） |
| version | INT NOT NULL DEFAULT 1 | 同 title 下递增 | 版本号：内容指纹不同则 +1 新行，指纹相同归并既有行 |
| visibility | TEXT NOT NULL DEFAULT 'internal' | CHECK in (public, internal, confidential) | 密级 |
| region | TEXT NOT NULL DEFAULT 'cn' | | 预留 |
| expire_at | TIMESTAMPTZ NULL | | 预留 |
| status | TEXT NOT NULL | CHECK in (processing, indexed, failed) | 处理状态机 |
| error | TEXT NULL | | 失败原因（status=failed 时） |
| created_at | TIMESTAMPTZ DEFAULT now() | | |

**状态机**：`processing → indexed`（全量块写入成功）；`processing → failed`
（任一步骤异常，error 记原因）；`failed → processing`（重新触发，同 version 覆盖）。

**同版本规则**：新上传的 content_hash 与该租户下既有文档相同 → 返回既有文档（不重复
入库）；不同 → 视为新版本（新行，version = 同 title 下 max(version)+1）。

### chunk（条款块）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID PK | | |
| doc_id | UUID FK → document ON DELETE CASCADE | | |
| tenant_id | TEXT NOT NULL | | 冗余存储，查询免 join 过滤 |
| chunk_type | TEXT NOT NULL | CHECK in (parent, child) | 父块/子块 |
| parent_id | UUID NULL FK → chunk | | 子块指向父块；父块为 NULL |
| sec_no | TEXT NULL | | 章节编号（如 `2.3.1`），引用展示用 |
| text | TEXT NOT NULL | | 本块文本（父块=完整小节；子块=用于检索） |
| embedding | VECTOR(1024) NULL | | 仅子块填充；维度=1024（research D2） |
| tsv | TSVECTOR NULL | GIN 索引 | 仅子块填充；jieba 预分词 + simple 配置 |
| metadata | JSONB NOT NULL DEFAULT '{}' | | {section_title, table_row, position} |
| created_at | TIMESTAMPTZ DEFAULT now() | | |

**索引**：
- `hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)` ON child
- `GIN (tsv)` ON child
- `BTREE (tenant_id, doc_id)`、`BTREE (parent_id)`

**不变式**：
- 父块无 embedding/tsv；子块必有 embedding + tsv + parent_id
- 文档删除（或版本覆盖）级联删除其全部块
- 子块 text 长度 50~1200 字符（切分校验）

### runtime_log（问答运行档案，FR-013）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGSERIAL PK | |
| trace_id | TEXT NOT NULL | `tr-<随机>`，返回给客户端（done 事件） |
| tenant_id | TEXT NOT NULL | |
| question | TEXT NOT NULL | |
| hit_count | INT | 拒答前命中条数 |
| top_score | REAL NULL | 重排最高分（拒答判定依据） |
| latency_ms | INT NOT NULL | 端到端 |
| refused | BOOLEAN NOT NULL | 是否拒答 |
| answer | TEXT NULL | 最终答案文本 |
| created_at | TIMESTAMPTZ DEFAULT now() | |

## 关系

```
document 1──N chunk（含父块行与子块行，子块 N──1 父块 chunk 自关联）
document 1──N runtime_log（逻辑关联：按 trace_id 时间窗归集，无外键）
```

## 校验规则（来自 spec FR）

| 规则 | 来源 |
|---|---|
| 重复上传同指纹文档 → 返回既有文档，不新增行 | FR-003 / clarify Q2 |
| status=failed 必有 error；indexed 必有 ≥1 父块与 ≥1 子块 | FR-002 |
| 引用必须由 chunk 行派生（doc title + sec_no + text 原句），禁止合成 | FR-006/007 |
| 重排 top score < 阈值（0.35 初值）→ 拒答路径 | FR-008 / research D4 |
| 所有查询强制 `tenant_id = :ctx` | 章程 V / FR-012 |
