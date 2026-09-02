#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校验 + 后处理：对 generate_kaihe_qa.py 的产物做溯源校验、去重、难度/公司统计，
并规则生成 L4 拒答题（测 RAG 系统是否"一本正经地胡说"）。

产物：
  - golden_qa_kaihe.clean.jsonl  清洗后（溯源不通过的剔除/标记）
  - golden_qa_kaihe.reject.jsonl 规则生成的 L4 拒答题
  - 控制台输出质量报告（难度分布 / 公司分布 / 溯源通过率）
"""

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # AtlasRAG/data/
RAW = BASE / "raw" / "kaihe_clauses.jsonl"
QA_IN = BASE / "evals" / "golden_qa_kaihe.jsonl"
QA_CLEAN = BASE / "evals" / "golden_qa_kaihe.clean.jsonl"
QA_REJECT = BASE / "evals" / "golden_qa_kaihe.reject.jsonl"


def build_doc_index():
    """doc_id → 全文，用于溯源子串匹配。"""
    idx = {}
    for l in RAW.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        r = json.loads(l)
        out = r["output"]
        lines = out.split("\n")
        product = lines[1].strip() if len(lines) > 1 else ""
        doc_id = re.sub(r"[^\w\u4e00-\u9fff]+", "_", product).strip("_")
        idx[doc_id] = out
    return idx


def norm_text(s):
    """去掉所有空白，用于容错子串匹配。"""
    return re.sub(r"\s+", "", s or "")


def quote_hit(quote, full_text):
    """quote 是否逐字出现在全文（容忍空白差异）。"""
    q = norm_text(quote)
    if len(q) < 4:  # 太短不做强校验
        return True
    return q in norm_text(full_text)


def reject_rules(product):
    """根据产品名识别险种，生成该险种"不可能有、但用户常误问"的拒答题。"""
    rules = [
        ("医疗", "这款医疗险确诊重大疾病后，一次性能赔多少保额？"),
        ("重疾", "这款重疾险住院期间每天能报销多少住院津贴？"),
        ("疾病", "这款疾病险住院期间每天能报销多少住院津贴？"),
        ("年金", "这款年金险确诊癌症后能赔多少重大疾病保险金？"),
        ("养老", "这款养老保险确诊癌症后能赔多少重大疾病保险金？"),
        ("意外", "这款意外险确诊癌症后能赔多少保险金？"),
        ("寿险", "这款寿险住院期间能报销多少医疗费？"),
    ]
    q = None
    for kw, question in rules:
        if kw in product:
            q = question
            break
    if q is None:
        q = "这款产品预期的年化收益率 / 分红率是多少？"
    return {
        "id": None,
        "question": q,
        "gold_answer": "知识库中无此信息，应明确告知无法回答，不得编造或猜测。",
        "difficulty": "L4",
        "category": "拒答边界",
        "source": [],
        "metadata": {
            "doc_id": re.sub(r"[^\w\u4e00-\u9fff]+", "_", product).strip("_"),
            "product": product,
            "company": "",
            "source_file": "rule_generated",
            "difficulty_review": False,
        },
    }


def main():
    doc_index = build_doc_index()
    qas = [json.loads(l) for l in QA_IN.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"输入 QA 总数：{len(qas)}")

    # 1) 溯源校验
    kept, dropped = [], []
    for qa in qas:
        doc_id = qa["metadata"]["doc_id"]
        full = doc_index.get(doc_id, "")
        srcs = qa.get("source") or []
        if not srcs or not full:
            kept.append(qa)
            continue
        ok = any(quote_hit(s.get("quote", ""), full) for s in srcs)
        if ok:
            kept.append(qa)
        else:
            dropped.append(qa)
    print(f"溯源通过：{len(kept)}  溯源失败(剔除)：{len(dropped)}")

    # 2) 去重（question 完全相同）
    seen, dedup = set(), []
    for qa in kept:
        q = qa["question"].strip()
        if q in seen:
            continue
        seen.add(q)
        dedup.append(qa)
    print(f"去重后：{len(dedup)}（去重 {len(kept) - len(dedup)} 条）")

    # 3) 编号
    for i, qa in enumerate(dedup, 1):
        qa["id"] = f"KAIHE-QA-{i:04d}"

    # 4) L4 拒答规则生成（每份条款一条）
    products = {}
    for qa in dedup:
        products[qa["metadata"]["doc_id"]] = qa["metadata"]["product"]
    rejects = []
    for doc_id, product in products.items():
        r = reject_rules(product)
        r["metadata"]["doc_id"] = doc_id
        r["metadata"]["company"] = doc_index.get(doc_id, "").split("\n")[0].strip()
        rejects.append(r)
    for i, r in enumerate(rejects, 1):
        r["id"] = f"KAIHE-REJECT-{i:04d}"

    # 5) 写入
    QA_CLEAN.parent.mkdir(parents=True, exist_ok=True)
    with open(QA_CLEAN, "w", encoding="utf-8") as f:
        for qa in dedup:
            f.write(json.dumps(qa, ensure_ascii=False) + "\n")
    with open(QA_REJECT, "w", encoding="utf-8") as f:
        for r in rejects:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 6) 统计报告
    diff = Counter(qa["difficulty"] for qa in dedup)
    comp = Counter(qa["metadata"]["company"] for qa in dedup)
    cat = Counter(qa.get("category", "其他") for qa in dedup)
    print("\n=== 难度分布 ===")
    for k in ["L0", "L1", "L3"]:
        print(f"  {k}: {diff.get(k, 0)}")
    print("=== 公司分布（Top 10）===")
    for c, n in comp.most_common(10):
        print(f"  {n:4d}  {c}")
    print("=== 主题标签分布（Top 15）===")
    for c, n in cat.most_common(15):
        print(f"  {n:4d}  {c}")
    print(f"\n产出：{QA_CLEAN}（{len(dedup)} 条）")
    print(f"      {QA_REJECT}（{len(rejects)} 条 L4 拒答）")


if __name__ == "__main__":
    main()
