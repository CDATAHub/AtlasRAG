# AtlasRAG

保险知识库的 Agentic RAG 平台：检索是 Agent 可随时调用的工具，评测闭环与合规是数据模型的一等公民。

## Language

**AgentLoop**:
Agent 的「感知-规划-执行-反思-收敛」循环。plan 产出计划，route 按计划逐步调度工具，reflect 决定收敛或重规划。
_Avoid_: pipeline、固定流程

**scope（权限域）**:
工具级权限声明（如 `retrieval:read`），来自 JWT claims，决定模型能看到哪些工具。
_Avoid_: 与文档密级混用

**visibility（密级）**:
文档级可见性（public / internal / confidential），决定工具能检索到哪些文档。

**Evidence**:
从工具原始返回中筛出、进入生成上下文的证据池（父块文本 + 来源 + 得分）。包含关系：tool_results ⊇ evidence ⊇ citations。
_Avoid_: 与 citations 混用

**Citations**:
最终答案实际引用的 Evidence 子集，用于前端溯源与引用评分。

**tool_results**:
工具的原始返回，append-only，仅用于调试、审计与失败归因。

**父子文档（parent/child chunk）**:
子块小而精确用于向量召回；父块是完整上下文，独立成行存储，子块经 `parent_id` 关联。命中子块后返回父块喂给 LLM。
_Avoid_: 在子块行冗余存父块全文

**闭卷集**:
不给证据片段、测真实检索召回的评测集（kaihe 102 份完整条款 + 985 条生成 QA + 51 条 L4 拒答题）。

**开卷集**:
自带证据片段、测生成质量与引用忠实度的评测集（InsQABench 1060 条）。

**回归评测（re-run）**:
同一评测 query 在新版本代码/配置上重新执行，对比效果涨跌。LLM 是被测变量，必须真跑。
_Avoid_: Replay（易与「录制响应原样重放」混淆；真回放 v1 不做）

**熔断线**:
超时/预算触发的强制降级输出线（如 20s），不是对外的延迟承诺值；延迟承诺见 02 NFR 分级口径。
_Avoid_: 把熔断线当 P95 目标

**文档版本（version）**:
DOCUMENT 的版本号，与 doc_id 组成索引幂等键，并纳入缓存键。

**InsQABench 字段约定（raw 数据）**:
`clause_subjective.json` 中 `p` = 题目所依据的证据段落原文（passage，含条款编号），
`answer` = 结构化参考答案（`[答案]`/`[证据]`/`[解释说明]` 三段）。

**kaihe 字段约定（raw 数据）**:
`kaihe_clauses.jsonl` 是「PDF 文本清洗」任务数据：`input` = 脏的 PDF 原样提取，
`output` = 清洗重排版（首行公司名、次行产品名）。入库与 QA 生成一律以
`output` 为底料（import_corpus.py 与 generate_kaihe_qa.py 同源）。
