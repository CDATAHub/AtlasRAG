# -*- coding: utf-8 -*-
"""
下载并体检 kaihe/chinese_insurance_doc_parsing 数据集（102 份完整保险条款）。

步骤：
1. 从 datasets-server API 已下载的 JSON 中提取 rows[].row
2. 归一化为 {split, instruction, input, output} 存到 raw/kaihe_clauses.jsonl
3. 从 output 中提取「公司全称(第1行)」「产品名(第2行)」「章节数」
4. 统计完整度 + 与 InsQABench 合同的公司重叠度

用法：python analyze_kaihe.py
"""
import json
import re
import collections
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
RAW = BASE / "raw"
TMP = Path(r"C:/Users/10268/WorkBuddy/2026-09-01-11-31-16/.insqa_tmp")
OUT_JSONL = RAW / "kaihe_clauses.jsonl"

RE_CLAUSE = re.compile(r"第[一二三四五六七八九十百\d]+条")

INSQA_COMPANIES = [
    "泰康", "安联", "PICC", "人保", "太平", "中华", "中美联泰", "大都会",
    "平安", "国寿", "人寿", "太平洋", "新华", "阳光", "众安", "天安", "大地",
]


def load_rows(path: Path):
    d = json.load(open(path, encoding="utf-8"))
    return [r["row"] for r in d.get("rows", [])]


def company_of(output: str) -> str:
    return output.split("\n", 1)[0].strip() if output else ""


def product_of(output: str) -> str:
    lines = [l for l in output.split("\n") if l.strip()]
    return lines[1].strip() if len(lines) > 1 else ""


def main():
    rows = []
    for split, f in [("train", "kaihe_train.json"), ("test", "kaihe_test.json")]:
        for r in load_rows(TMP / f):
            rows.append({"split": split, **r})

    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"已归一化 {len(rows)} 条 → {OUT_JSONL}\n")

    # 完整度统计
    out_lens = [len(r["output"]) for r in rows]
    in_lens = [len(r["input"]) for r in rows]
    clause_counts = [len(RE_CLAUSE.findall(r["output"])) for r in rows]
    import statistics
    print("=== 完整度 ===")
    print(f"  output(整理后条款) 字符数: 最小 {min(out_lens)} / 中位 {statistics.median(out_lens):.0f} / 最大 {max(out_lens)}")
    print(f"  input(原始pdfminer) 字符数: 最小 {min(in_lens)} / 中位 {statistics.median(in_lens):.0f} / 最大 {max(in_lens)}")
    print(f"  章节数(第N条计数): 最小 {min(clause_counts)} / 中位 {statistics.median(clause_counts):.0f} / 最大 {max(clause_counts)}")
    print(f"  output >= 1万字: {sum(1 for x in out_lens if x >= 10000)} 份")
    print(f"  output >= 5000字: {sum(1 for x in out_lens if x >= 5000)} 份")
    print(f"  output < 3000字:  {sum(1 for x in out_lens if x < 3000)} 份\n")

    # 公司分布
    comps = collections.Counter()
    for r in rows:
        c = company_of(r["output"])
        comps[c if c else "（无公司名）"] += 1
    print("=== 公司分布（output 第1行）===")
    for c, n in comps.most_common():
        print(f"  {n:3d}  {c}")

    # 与 InsQABench 公司重叠
    print("\n=== 与 InsQABench 公司重叠 ===")
    overlap = sum(1 for r in rows if any(k in company_of(r["output"]) for k in INSQA_COMPANIES))
    print(f"  kaihe 的 {len(rows)} 份中，公司名能命中 InsQABench 常见公司的: {overlap} 份")

    # 抽样看质量
    print("\n=== 抽样 2 条看实际质量 ===")
    for idx in [0, 50]:
        if idx >= len(rows):
            break
        r = rows[idx]
        out = r["output"]
        print(f"\n----- [{idx}] company={company_of(out)!r} product={product_of(out)!r} -----")
        print(f"output 前 500 字:\n{out[:500]}")
        print(f"...（共 {len(out)} 字）")


if __name__ == "__main__":
    main()
