#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parse_insqa.py — 把 InsQABench「保险合同问答」数据解析成 AtlasRAG 阶段 0 产物。

输入 (data/raw/):
  - clause_objective.json  : list[ {input, output, id} ]   960 条客观题
  - clause_subjective.json : dict[合同名 -> {问题 -> {p, answer}}]  100 条主观题(20 合同 x 5 问)

输出:
  - data/evals/golden_qa.jsonl   黄金问答对(五元组), 每行一条 JSON
  - data/corpus/<doc_id>.txt     按合同聚合的证据片段(路线 A 的片段级语料)
  - data/corpus_manifest.json    语料清单(来源/许可/规模)

五元组字段:
  id, question, gold_answer, difficulty, category, source[], metadata
"""
import json
import re
import hashlib
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]  # AtlasRAG/data
RAW = BASE / "raw"
EVALS = BASE / "evals"
CORPUS = BASE / "corpus"

# 许可证来源说明(附在 manifest)
SOURCE_NOTICE = {
    "dataset": "InsQABench (https://github.com/jingjingjing-ding/InsQABench)",
    "license": "Apache-2.0",
    "paper": "arXiv:2501.10943",
    "authors": "华中科技大学 VLR Lab",
}


def slug_doc(name: str) -> str:
    """由合同名生成稳定 doc_id。"""
    h = hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
    return f"doc-{h}"


def categorize(contract: str) -> str:
    """按合同名粗分险种。"""
    pairs = [
        ("医疗", "医疗险"), ("意外", "意外险"), ("重疾", "重疾险"),
        ("年金", "年金险"), ("寿险", "寿险"), ("定期", "寿险"),
        ("终身", "寿险"), ("旅行", "出行险"), ("交通", "出行险"),
        ("宠物", "宠物险"), ("财产", "财产险"), ("一切险", "财产险"),
        ("出租", "财产险"), ("盗抢", "财产险"),
    ]
    for kw, cat in pairs:
        if kw in contract:
            return cat
    return "其他"


def heuristic_difficulty(question: str) -> str:
    """粗粒度难度启发式: 含综合/多跳信号词 -> L1, 否则 L0。L2~L4 需人工补充。"""
    multi_kw = ("哪些", "分别", "如何", "为什么", "什么情况", "什么条件",
                "区别", "包含", "需要满足", "同时", "是否", "可以", "能否")
    for kw in multi_kw:
        if kw in question:
            return "L1"
    return "L0"


def parse_objective(item: dict):
    """解析 clause_objective.json 一条 -> (contract, question, answer, evidence, glossary)。"""
    raw_in = item.get("input", "") or ""
    raw_out = item.get("output", "") or ""

    contract = "未知合同"
    m = re.search(r"这段文字来自的合同是：(.+?)(?:\n|$)", raw_in)
    if m:
        contract = m.group(1).strip()

    evidence = ""
    m = re.search(r"片段内容：(.+?)\n用户提问：", raw_in, re.DOTALL)
    if m:
        evidence = m.group(1).strip()

    question = ""
    m = re.search(r"用户提问：(.+)", raw_in, re.DOTALL)
    if m:
        question = m.group(1).strip()

    gold_answer, glossary = raw_out.strip(), None
    if "**专有名词解释**" in raw_out:
        gold_answer, glossary = raw_out.split("**专有名词解释**", 1)
        gold_answer = gold_answer.strip()
        glossary = glossary.strip().lstrip("：:\n ") or None

    return contract, question, gold_answer, evidence, glossary


def parse_subjective(contract: str, qa_dict: dict):
    """解析 clause_subjective.json 一个合同 -> list[(contract, question, answer, evidence, glossary)]。"""
    results = []
    for question, v in qa_dict.items():
        evidence = (v.get("p") or "").strip()
        raw_answer = v.get("answer") or ""

        gold_answer = raw_answer.strip()
        m = re.search(r"\[答案\]\s*:?\s*(.+?)(?:\n\n\[证据\]|\Z)", raw_answer, re.DOTALL)
        if m:
            gold_answer = m.group(1).strip()

        glossary = None
        m2 = re.search(r"\[解释说明\]\s*:?\s*(.+)", raw_answer, re.DOTALL)
        if m2:
            glossary = m2.group(1).strip().lstrip("：:\n ") or None

        results.append((contract, question, gold_answer, evidence, glossary))
    return results


def build_record(idx, contract, question, gold_answer, evidence, glossary,
                 source_file, orig_id) -> dict:
    doc_id = slug_doc(contract)
    return {
        "id": f"QA-{idx:04d}",
        "question": question,
        "gold_answer": gold_answer,
        "difficulty": heuristic_difficulty(question),
        "category": categorize(contract),
        "source": [{"doc_id": doc_id, "quote": evidence}],
        "metadata": {
            "contract": contract,
            "source_file": source_file,
            "orig_id": orig_id,
            "glossary": glossary,
            "difficulty_review": True,  # 难度为启发式, 需人工复核
        },
    }


def main():
    records = []
    corpus_map = {}  # doc_id -> {"name": contract, "chunks": set()}

    # --- 客观题 ---
    obj_path = RAW / "clause_objective.json"
    if obj_path.exists():
        obj = json.loads(obj_path.read_text(encoding="utf-8"))
        for item in obj:
            contract, q, a, ev, gl = parse_objective(item)
            records.append(build_record(
                len(records) + 1, contract, q, a, ev, gl,
                "clause_objective", item.get("id")))
            d = slug_doc(contract)
            corpus_map.setdefault(d, {"name": contract, "chunks": set()})
            if ev:
                corpus_map[d]["chunks"].add(ev)
    else:
        print(f"[warn] 缺少 {obj_path}")

    # --- 主观题 ---
    sub_path = RAW / "clause_subjective.json"
    if sub_path.exists():
        sub = json.loads(sub_path.read_text(encoding="utf-8"))
        for contract, qa_dict in sub.items():
            for contract2, q, a, ev, gl in parse_subjective(contract, qa_dict):
                records.append(build_record(
                    len(records) + 1, contract2, q, a, ev, gl,
                    "clause_subjective", None))
                d = slug_doc(contract2)
                corpus_map.setdefault(d, {"name": contract2, "chunks": set()})
                if ev:
                    corpus_map[d]["chunks"].add(ev)
    else:
        print(f"[warn] 缺少 {sub_path}")

    # --- 写 golden_qa.jsonl ---
    EVALS.mkdir(parents=True, exist_ok=True)
    out_qa = EVALS / "golden_qa.jsonl"
    with out_qa.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # --- 写 corpus 片段语料 + manifest ---
    CORPUS.mkdir(parents=True, exist_ok=True)
    manifest = []
    for doc_id, info in sorted(corpus_map.items()):
        chunks = sorted(info["chunks"])
        text = "\n\n".join(chunks)
        (CORPUS / f"{doc_id}.txt").write_text(text, encoding="utf-8")
        manifest.append({
            "doc_id": doc_id,
            "title": info["name"],
            "chunk_count": len(chunks),
            "char_count": len(text),
            "source": "InsQABench clause QA (evidence 片段聚合)",
            "note": "片段级语料(路线A); 完整合同 PDF 待路线B补充",
        })

    man_path = BASE / "corpus_manifest.json"
    payload = {
        **SOURCE_NOTICE,
        "doc_count": len(manifest),
        "total_qa": len(records),
        "docs": manifest,
    }
    man_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- 汇总 ---
    from collections import Counter
    diff = Counter(r["difficulty"] for r in records)
    cat = Counter(r["category"] for r in records)
    print(f"总计 QA: {len(records)} 条")
    print(f"难度分布: {dict(diff)}")
    print(f"险种分布: {dict(cat)}")
    print(f"文档数: {len(manifest)}")
    print(f"已写出: {out_qa}")
    print(f"已写出: {man_path}")
    # 抽样展示一条
    if records:
        print("\n--- 抽样第 1 条 ---")
        print(json.dumps(records[0], ensure_ascii=False, indent=2)[:800])


if __name__ == "__main__":
    main()
