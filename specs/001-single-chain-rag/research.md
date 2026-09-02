# Phase 0 Research: 单链路 RAG

> 所有选型均已在 docs/02·05 的 ADR 与 grilling 决策中确定，本文件将其落为本阶段的
> 执行决策（Decision / Rationale / Alternatives），并消解 plan 中的全部未知项。
> 未新增 NEEDS CLARIFICATION。

## D1. 向量索引与过滤

- **Decision**: pgvector HNSW（cosine）+ `hnsw.iterative_scan`（pgvector ≥ 0.8）+
  超采样（fetch top-50 实际按 3× 候选再过滤），tenant/visibility 过滤在 SQL 层强制。
- **Rationale**: docs/05 §5.6 既定；HNSW 索引扫描先于过滤导致结果不足 top_k，
  迭代扫描是官方对策；单租户演示规模无需分区。
- **Alternatives**: Qdrant（额外服务，违背单机 Compose 约束）；IVFFlat（召回低于 HNSW）。

## D2. Embedding 模型与维度档位

- **Decision**: 百炼 `qwen3.7-text-embedding`，**1024 维档位**（该模型支持
  2560/1024/768）。
- **Rationale**: 演示语料规模（<1000 块/文档量级）1024 已饱和；2560 使向量存储与
  HNSW 内存 ×2.5 而收益存疑；建库后换档位 = 全量重建（docs/05 §5.6），故先定 1024。
- **Alternatives**: 2560 维（最终态可升，验收不依赖）；bge-m3 本地（引入 GPU/CPU 推理
  运维，与托管 API 决策冲突）。

## D3. 关键词检索（BM25）实现

- **Decision**: Postgres 原生 FTS：入库时 jieba 预分词（加载保险术语自定义词典：
  等待期/免赔额/犹豫期/宽限期/现金价值…），空格拼接后 `to_tsvector('simple', …)` 存列 +
  GIN 索引；排序用 `ts_rank_cd`。
- **Rationale**: ADR-008；零扩展依赖（不装 zhparser/pg_jieba，Compose 镜像用官方
  postgres+pgvector 即可）；预分词在写入侧一次完成，查询侧同规则分词。
- **Alternatives**: ParadeDB pg_search（原生 BM25 分数，演进项）；zhparser（需自建镜像）。

## D4. 融合与重排

- **Decision**: RRF（k=60）融合两路 top-50 → 百炼 `qwen3.7-text-rerank` 精排 → top-5；
  拒答信号 = 重排后最高分低于阈值（阈值由闭卷集校准，初值 0.35，验收前用
  L4 拒答题调至 SC-002 ≥ 90%）。
- **Rationale**: docs/05 §5.4/5.5 既定；阈值放在重排分而非召回分上，因其语义相关性
  最强、跨查询可比。
- **Alternatives**: 加权线性融合（需调权重，RRF 免调）；仅在召回分上设阈值（不可比）。

## D5. 切分策略

- **Decision**: 父块 = 章节小节（「1 / 1.1 / 2.3.1」编号识别，含完整表格）；子块 =
  300~500 token、10% 重叠，表格按行切；子块行存 `parent_id`，父块独立成行。
- **Rationale**: docs/05 §5.3；kaihe 条款实测章节编号完整（data/README 质检），
  正则解析可靠；父块独立成行避免 parent_text 冗余（07 章数据模型）。
- **Alternatives**: 固定长度滑窗（破坏条款语义）；固定层级取二级标题（粒度过粗）。

## D6. 外部服务客户端与 mock 基准

- **Decision**: 三客户端（Embedding/Rerank/LLM）各定义一个 `Protocol` +
  请求/响应 Pydantic 模型；DashScope OpenAI 兼容模式实现生产版；测试用
  `FakeEmbedding/FakeRerank/FakeLLM`（确定性、可编程返回），夹具来自真实响应录制
  （tests/fixtures/）。LLM SSE 生成走增量 chunk 协议。
- **Rationale**: 章程 VI（契约解耦）+ VII（mock 以契约为基准）；依赖注入使测试
  零真实调用。
- **Alternatives**: 直接 SDK 调用（无法 mock）；仅 mock HTTP 层（respx）与业务语义脱节。

## D7. 鉴权

- **Decision**: 自签 HS256 JWT（PyJWT），claims：`tenant_id` / `scopes` / `exp`；
  签发脚本 `scripts/issue_token.py`（读 .env 密钥）；FastAPI 依赖注入式校验，
  三接口统一 `TenantContext`。
- **Rationale**: clarify Q3 决策；章程 V + ADR-009；HS256 单机演示足够（RS256 留演进）。
- **Alternatives**: 静态 Key（偏离章程 V）；OIDC（超范围）。

## D8. 运行档案

- **Decision**: `runtime_log` 表：trace_id、question、hit_count、latency_ms、refused、
  top_score、tenant_id、created_at；问答完成时同步写入。
- **Rationale**: clarify Q4；表结构可查询、与 docs/07 trace 设计衔接；阶段 1 不引入
  Langfuse。
- **Alternatives**: JSONL 文件（不可 SQL 聚合）；Langfuse（阶段 5 范围）。

## D9. 检索评测（验收门禁）

- **Decision**: `scripts/run_retrieval_eval.py`：对闭卷集 985 条逐题检索（不调 LLM），
  命中判定 = 标准原文 quote（规范化空白后）出现在 top-5 结果之一的父块文本中；
  51 条 L4 仅统计拒答率（不比对命中）。输出 JSON 报告 + 失败案例清单（问题/期望/
  实际 top5）。分难度（L0/L1/L3）聚合。
- **Rationale**: spec Assumptions 既定口径；无 LLM 调用 → 免费、快速、可进 CI。
- **Alternatives**: 全量生成后判分（贵且慢，属阶段 4 抽样/nightly 范围）。

## D10. 文档处理异步化

- **Decision**: 进程内 `asyncio.create_task` 后台处理 + `document.status` 状态机
  （processing → indexed / failed），失败记录原因、支持重新触发；不引入 Celery/RQ。
- **Rationale**: 阶段 1 单机演示、吞吐要求低（SC-005 仅 5 分钟）；少一个消息队列组件。
- **Alternatives**: Celery+Redis（阶段 5 多副本时演进）；同步处理（阻塞请求，违反
  5 分钟窗口下的响应体验）。

## D11. API 风格

- **Decision**: FastAPI + `StreamingResponse`（`text/event-stream`）实现问答 SSE；
  事件序裁剪自 docs/08 §8.2：`evidence → answer(delta*) → citations → done`
  （无 plan/reflect 事件——属阶段 2 节点）。上传用 multipart；状态查询 JSON。
- **Rationale**: clarify Q1（最小三接口）；prototype 已按该事件序实现，直接对接。
- **Alternatives**: WebSocket（重）；一次性 JSON 返回（丢失流式体验，原型需改）。
