# -*- coding: utf-8 -*-
"""
从 golden_qa.jsonl 导出「必采合同清单」CSV。

用途：阶段 0 路线 B 的第一步 —— 以合同名为对齐锚点，按单合同 QA 密度分层，
产出「该先采哪些完整合同 PDF」的清单。

输出字段：
  tier              采集优先级 (Tier1 必采 / Tier2 扩展 / Tier3 长尾 / Tier4 可缓)
  qa_count          该合同在黄金问答集中的问答条数
  contract_name     合同名（同时是 doc_id 锚点）
  company           保险公司（启发式匹配）
  filing_no         监管备案文号（正则提取，用于官方披露系统精确检索）
  cum_qa_pct        累计覆盖 QA 占比（按条数降序累计）

用法：
  python export_contract_list.py
"""
import json
import csv
import re
import collections
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
QA_FILE = BASE / "evals" / "golden_qa.jsonl"
OUT_CSV = BASE / "contract_fetch_list.csv"

# 备案文号：如「泰康养发[2022]130号」「(2021)xxx号」；日期版本：如「2020-01-19」
RE_FILING = re.compile(r"\[\d{4}\]\d+号|\(\d{4}\)\d+号|【\d{4}】\d+号")
RE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

COMPANY_KW = [
    "泰康", "安联", "PICC", "人保", "太平", "TaiPing", "中华财险", "中美联泰",
    "大都会", "平安", "国寿", "中国人寿", "太平洋", "新华", "阳光", "众安",
    "天安", "大地", "都邦", "华泰", "永安", "中意", "中英", "同方全球", "友邦",
    "复星", "和谐", "昆仑", "珠江", "利宝", "京东安联", "史带", "富德", "合众",
    "幸福", "华夏", "信泰", "百年", "恒安标准", "招商信诺",
]


def company(name: str) -> str:
    n = name.strip("《》 ")
    for kw in COMPANY_KW:
        if kw in n:
            return kw
    return "其他"


def filing_no(name: str) -> str:
    m = RE_FILING.search(name)
    if m:
        return m.group(0)
    d = RE_DATE.search(name)
    return d.group(0) if d else ""


def main():
    contracts = collections.Counter()
    for line in open(QA_FILE, encoding="utf-8"):
        r = json.loads(line)
        contracts[r["metadata"].get("contract") or ""] += 1

    total = sum(contracts.values())
    ordered = contracts.most_common()

    # 分层阈值（按累计覆盖 QA 占比）
    tiers = [
        ("Tier1 必采", 0.30),
        ("Tier2 扩展", 0.60),
        ("Tier3 长尾", 0.80),
    ]

    rows = []
    cum = 0
    for name, n in ordered:
        cum += n
        pct = cum / total
        tier = "Tier4 可缓"
        for tname, th in tiers:
            if pct <= th + 1e-9:
                tier = tname
                break
        rows.append({
            "tier": tier,
            "qa_count": n,
            "contract_name": name,
            "company": company(name),
            "filing_no": filing_no(name),
            "cum_qa_pct": round(pct, 4),
        })

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=[
            "tier", "qa_count", "contract_name", "company", "filing_no", "cum_qa_pct"])
        w.writeheader()
        w.writerows(rows)

    # 统计
    stat = collections.Counter(r["tier"] for r in rows)
    print(f"已导出 {len(rows)} 份合同 → {OUT_CSV}")
    print(f"总 QA: {total}")
    for t in ["Tier1 必采", "Tier2 扩展", "Tier3 长尾", "Tier4 可缓"]:
        cnt = stat.get(t, 0)
        qa = sum(r["qa_count"] for r in rows if r["tier"] == t)
        print(f"  {t}: {cnt} 份 / {qa} 条 QA")
    print("\n=== Tier1 必采清单（前 20）===")
    for r in rows[:20]:
        if r["tier"] == "Tier1 必采":
            fn = f" 文号={r['filing_no']}" if r["filing_no"] else ""
            print(f"  [{r['qa_count']:2d}条] {r['company']:<6} {r['contract_name'][:44]}{fn}")


if __name__ == "__main__":
    main()
