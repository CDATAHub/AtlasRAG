# AtlasRAG 阶段 0 · 数据集说明

> 语料来源：**InsQABench**（华中科技大学 VLR Lab，Apache-2.0）
> 论文：arXiv:2501.10943 ｜ 仓库：https://github.com/jingjingjing-ding/InsQABench

## 目录结构

```
data/
├── raw/                      # 原始数据（未改动）
│   ├── clause_objective.json   # 960 条客观题
│   ├── clause_subjective.json  # 100 条主观题（20 合同 × 5 问）
│   └── kaihe_clauses.jsonl     # kaihe 完整条款 102 份（路线 B 文档池）
├── scripts/
│   ├── parse_insqa.py          # 解析脚本（可复跑）
│   ├── export_contract_list.py # 导出必采合同清单 CSV
│   └── analyze_kaihe.py        # kaihe 数据集下载+质量体检
├── contract_fetch_list.csv     # 必采合同清单（tier 分层+备案文号）
├── evals/
│   └── golden_qa.jsonl         # ★ 黄金问答对（1060 条）
├── corpus/
│   └── doc-<hash>.txt          # 按合同聚合的证据片段（378 份）
└── corpus_manifest.json        # 语料清单 + 来源/许可/规模
```

## 黄金问答对字段（golden_qa.jsonl，每行一条）

| 字段 | 说明 |
|---|---|
| `id` | `QA-0001` ~ `QA-1060` |
| `question` | 用户提问 |
| `gold_answer` | 标准答案（已剔除术语解释，纯答案） |
| `difficulty` | L0/L1（启发式，**需复核**，见下） |
| `category` | 险种粗分类（医疗/意外/寿险/年金/财产/出行/宠物/其他） |
| `source` | `[{doc_id, quote}]` 溯源：证据段落原文 |
| `metadata.contract` | 原始合同名 |
| `metadata.glossary` | 专有名词解释（保险 QA 加分项） |
| `metadata.difficulty_review` | `true` = 难度标签为启发式，待人工复核 |

## 规模统计（解析实测）

- 总 QA：**1060 条**（客观 960 + 主观 100）
- 文档数：**378 份**合同条款
- 难度分布：L0 × 487，L1 × 573
- 险种分布：医疗 310 / 财产 142 / 意外 150 / 寿险 124 / 年金 121 / 出行+宠物等 / 其他 213

## ⚠️ 三个已知限制（必须知道）

1. **难度标签是启发式初稿**：脚本按问题关键词粗分 L0/L1，未人工精修。`difficulty_review: true` 标记了所有待复核项。
2. **缺 L2/L3/L4**：InsQABench 是「给定证据片段 → 找答案」的阅读理解格式，天然没有**多跳推理(L2)、表格数值(L3)、拒答边界(L4)**。这三类需要基于完整文档人工补充（目标：L0~L4 每级 ≥3 条）。
3. **语料是「片段级」而非「完整文档」**：`corpus/` 里是证据片段聚合，不是完整合同。用它评测，检索环节是「开卷」的——证据已被提前给出。**要测真实检索召回，必须走路线 B 补完整合同 PDF。**

## 复用方式

重新解析（改了脚本后）：

```bash
python3 data/scripts/parse_insqa.py
```

## kaihe 完整条款数据集（路线 B 文档池，已落地）

> 来源：`kaihe/chinese_insurance_doc_parsing`（HuggingFace，Apache-2.0，102 份）
> 清洗自天池实验室公共数据集，`output` 字段是**整理后的完整条款全文**。

**质量实测（analyze_kaihe.py）**：

| 指标 | 数值 |
|---|---|
| 条款数 | 102 份（train 70 + test 32） |
| output 字符数 | 最小 3041 / 中位 7602 / 最大 34126 |
| ≥1 万字 | 20 份 ｜ ≥5000 字 82 份 ｜ <3000 字 0 份 |
| 章节结构 | 「1 / 1.1 / 2.3.1」数字编号，完整 |
| 公司覆盖 | 人保寿险 24、太平洋 22、泰康 26、中华联合 16、中国人寿 12、太保安联 2 |

**与 InsQABench 的重叠：仅 2/102（≈2%）**。两套数据产品名几乎不重叠——kaihe 是「完整文档但无 QA」，InsQABench 是「有 QA 但无完整文档」。**因此 kaihe 不能直接复用 InsQABench 的 1060 条 QA，需为 kaihe 单独生成 QA。**

## 路线 B 结论（阶段 0 收尾）

1. **行业协会产品信息库**：找不到 InsQABench 对应合同，且不提供下载 → 排除。
2. **片段模拟生成完整 PDF**：❌ 不可行。片段聚合平均仅 959 字/合同（真实条款 1~3 万字），拼出来是「残片」；LLM 补全 = 编造，污染评测。
3. **根因**：InsQABench 是「开卷」数据集（每题自带答案片段），天生不适合测闭卷检索。
4. **✅ 最终方案——分离两个评测目标**：
   - **检索召回（闭卷）**：用 kaihe 102 份完整条款当 corpus，为其单独生成 QA；
   - **生成 + 引用质量（开卷）**：继续用 InsQABench 1060 条。
