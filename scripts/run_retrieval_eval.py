#!/usr/bin/env python3
"""闭卷集检索评测（US4 / SC-001/002，research D9/D11）。

首轮无 LLM 调用（章程 II 确定性门禁）：对每题做检索（可配 --no-rerank 跳过重排），
命中判定 = 标准原文 quote（规范化空白）出现在 top-5 结果之一的父块原文中。
L4 子集统计拒答率；--calibrate 用 L4 扫拒答阈值（仅 rerank 模式有意义）。

--loop（SC-002 / clarify Q4）：首轮结束后仅对失败集开启反思回环（plan/reflect 真调
LLM，成本 = 失败集规模），报告产出 repair_rate = 回环修复数 ÷ 同配置首轮失败数。

用法：
  python scripts/run_retrieval_eval.py \
    --dataset data/evals/golden_qa_kaihe.clean.jsonl --output report.json
  python scripts/run_retrieval_eval.py --limit 200 --no-rerank   # 抽样 + 跳过重排
  python scripts/run_retrieval_eval.py --loop --output report_loop.json
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import get_settings  # noqa: E402
from src.data.db import build_sessionmaker  # noqa: E402
from src.eval.matching import is_fact_hit, is_quote_hit  # noqa: E402
from src.rag.hybrid import hybrid_search  # noqa: E402
from src.rag.rerank import rerank_hits  # noqa: E402
from src.services.clients.embedding import DashscopeEmbedding  # noqa: E402
from src.services.clients.llm import DashscopeLlm  # noqa: E402
from src.services.clients.rerank import DashscopeRerank  # noqa: E402


async def evaluate_one(session, embedding, reranker, tenant, item, settings, threshold):
    question = item["question"]
    vec = (await embedding.embed([question]))[0]
    hits = await hybrid_search(session, tenant, question, vec, settings.hybrid_top_k)

    if settings.use_rerank:
        ranked = await rerank_hits(reranker, question, hits, settings.rerank_top_k)
        eff_threshold = threshold  # 语义：重排相关性分（0~1）
    else:
        ranked = hits[: settings.rerank_top_k]
        for hit in ranked:
            hit["score"] = float(hit.get("rrf_score", 0.0))
        eff_threshold = 0.0  # RRF 分无相关性语义：仅零命中拒答

    top_score = float(ranked[0]["score"]) if ranked else None
    refused = (top_score is None) or top_score < eff_threshold
    parent_texts = [h["parent_text"] for h in ranked]
    quotes = [s.get("quote", "") for s in item.get("source", [])]
    quote_hit = any(is_quote_hit(q, parent_texts) for q in quotes if q)
    fact_hit = is_fact_hit(item.get("gold_answer", ""), parent_texts)
    hit = quote_hit if fact_hit is None else fact_hit  # 验收口径：事实级；无事实回退 quote
    return hit, refused, top_score, quote_hit, [
        {"title": h["title"], "sec_no": h.get("sec_no"), "score": round(float(h["score"]), 4)}
        for h in ranked
    ]


async def loop_repair(
    session, items: list[dict], settings, tenant: str, embedding, reranker
) -> list[dict]:
    """对失败集跑 AgentLoop 回环（research D11）：plan/reflect 真调 LLM，判据同首轮。"""
    from langgraph.checkpoint.memory import MemorySaver

    from src.agent.graph import build_graph, build_tool_registry

    llm = DashscopeLlm(
        settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_max_tokens
    )
    graph = build_graph(
        llm=llm,
        registry=build_tool_registry(embedding, reranker, settings),
        settings=settings,
        checkpointer=MemorySaver(),
    )
    cases = []
    for item in items:
        quotes = [s.get("quote", "") for s in item.get("source", []) if s.get("quote")]
        try:
            final = await graph.ainvoke(
                {
                    "question": item["question"],
                    "tenant_id": tenant,
                    "trace_id": "eval-loop",
                    "session_id": "eval",
                    "message_id": str(item.get("id") or ""),
                    "client_msg_id": str(item.get("id") or ""),
                    "messages": [{"role": "user", "content": item["question"]}],
                },
                {"configurable": {"thread_id": f"eval:{item.get('id')}", "db": session}},
            )
            hits = [h for e in final.get("evidence") or [] for h in e.get("hits") or []]
            parent_texts = [h["parent_text"] for h in hits]
            repaired = bool(quotes) and any(is_quote_hit(q, parent_texts) for q in quotes)
            rounds = int(final.get("plan_rounds") or 1)
            queries = [t.get("query") for t in final.get("tool_results") or []]
        except Exception as exc:  # noqa: BLE001 —— 单题失败不影响整体
            repaired, rounds, queries = False, 0, [f"error: {exc}"[:120]]
        cases.append(
            {"id": item.get("id"), "question": item["question"], "repaired": repaired,
             "rounds": rounds, "queries": queries}
        )
        await asyncio.sleep(0.25)
    return cases


async def run(
    dataset: str,
    tenant: str,
    output: str | None,
    limit: int | None,
    calibrate: bool,
    no_rerank: bool = False,
    loop: bool = False,
) -> int:
    settings = get_settings()
    if no_rerank:
        settings.use_rerank = False  # 本轮评测跳过重排（记录于报告）
    session_factory = build_sessionmaker(settings.database_url)
    embedding = DashscopeEmbedding(
        settings.llm_base_url, settings.llm_api_key, settings.embedding_model, settings.embedding_dim
    )
    reranker = DashscopeRerank(
        settings.rerank_endpoint, settings.llm_api_key, settings.rerank_model
    )

    items = [json.loads(l) for l in Path(dataset).read_text(encoding="utf-8").splitlines() if l.strip()]
    # L4 拒答题独立成文件（golden_qa_kaihe.reject.jsonl），默认合并参与拒答率统计
    l4_path = Path(dataset).parent / "golden_qa_kaihe.reject.jsonl"
    if l4_path.exists() and not calibrate:
        items += [json.loads(l) for l in l4_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if limit:
        # 随机抽样（固定种子可复现）：避免顺序取样的文档偏斜
        import random

        rng = random.Random(42)
        l4 = [i for i in items if i.get("difficulty") == "L4"]
        rest = [i for i in items if i.get("difficulty") != "L4"]
        items = rng.sample(rest, max(0, limit - len(l4))) + l4
    print(f"评测集：{len(items)} 条 | rerank：{settings.use_rerank}")

    results = []
    async with session_factory() as session:
        for idx, item in enumerate(items, 1):
            try:
                hit, refused, top_score, top5, quote_hit = await evaluate_one(
                    session, embedding, reranker, tenant, item, settings, settings.refusal_threshold
                )
            except Exception as exc:  # noqa: BLE001 —— 单题失败记为失败案例，不中断
                hit, refused, top_score, quote_hit, top5 = (
                    False,
                    False,
                    None,
                    False,
                    [{"error": f"{type(exc).__name__}: {exc}"[:200]}],
                )
            await asyncio.sleep(0.25)  # 低于百炼 QPM 限流阈值
            results.append(
                {
                    **item,
                    "_hit": hit,
                    "_quote_hit": quote_hit,
                    "_refused": top_score is None or refused,
                    "_top_score": top_score,
                    "_top5": top5,
                }
            )
            if idx % 50 == 0:
                print(f"进度 {idx}/{len(items)}")

    l4 = [r for r in results if r.get("difficulty") == "L4"]
    normal = [r for r in results if r.get("difficulty") != "L4"]

    def recall(rows, key="_hit"):
        return round(sum(1 for r in rows if r[key]) / len(rows), 4) if rows else None

    report = {
        "dataset": dataset,
        "total": len(results),
        "use_rerank": settings.use_rerank,
        "recall_at_5": recall(normal),  # 事实级（验收口径）
        "recall_at_5_quote": recall(normal, "_quote_hit"),  # quote 级（参考）
        "by_difficulty": {
            d: recall([r for r in normal if r.get("difficulty") == d])
            for d in ("L0", "L1", "L3")
        },
        "l4_refusal_rate": round(sum(1 for r in l4 if r["_refused"]) / len(l4), 4) if l4 else None,
        "false_refusal_rate": round(
            sum(1 for r in normal if r["_refused"]) / len(normal), 4
        ) if normal else None,
        "threshold": settings.refusal_threshold,
        "failures": [
            {
                "id": r.get("id"),
                "question": r["question"],
                "expected_quotes": [s.get("quote") for s in r.get("source", [])],
                "top5": r["_top5"],
            }
            for r in results if not r["_hit"] and r.get("difficulty") != "L4"
        ],
    }
    print(f"\nRecall@5 整体（事实级）：{report['recall_at_5']}（门槛 0.8）｜quote 级参考：{report['recall_at_5_quote']}")
    print(f"分难度：{report['by_difficulty']}")
    print(f"L4 拒答率：{report['l4_refusal_rate']}（门槛 0.9）｜误拒率：{report['false_refusal_rate']}")
    print(f"失败案例：{len(report['failures'])} 条")

    if loop and report["failures"]:
        print(f"\n—— 回环修复（仅失败集 {len(report['failures'])} 条，plan/reflect 真调 LLM）——")
        async with session_factory() as session:
            by_id = {i.get("id"): i for i in items if i.get("difficulty") != "L4"}
            failed_items = [by_id[f["id"]] for f in report["failures"] if f["id"] in by_id]
            cases = await loop_repair(
                session, failed_items, settings, tenant, embedding, reranker
            )
        repaired = sum(1 for c in cases if c["repaired"])
        report["loop"] = {
            "failures": len(cases),
            "repaired": repaired,
            "repair_rate": round(repaired / len(cases), 4) if cases else None,
            "cases": cases,
        }
        print(f"回环修复：{repaired}/{len(cases)} = {report['loop']['repair_rate']}（SC-002 门槛 0.3）")

    if calibrate and l4 and settings.use_rerank:
        print("\n—— L4 拒答阈值扫描（threshold → 拒答率 / 误拒率）——")
        for th in [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.60]:
            refused4 = sum(1 for r in l4 if r["_top_score"] is None or r["_top_score"] < th) / len(l4)
            false_ref = sum(
                1 for r in normal if r["_top_score"] is None or r["_top_score"] < th
            ) / max(1, len(normal))
            print(f"  {th:.2f} → 拒答 {refused4:.3f} / 误拒 {false_ref:.3f}")

    if output:
        Path(output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n报告已写入 {output}")
    recall_value = report["recall_at_5"] or 0
    return 0 if recall_value >= 0.8 else 1  # 非零退出码 → CI 阻断（T046）


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/evals/golden_qa_kaihe.clean.jsonl")
    parser.add_argument("--tenant", default="tenant-001")
    parser.add_argument("--output", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--no-rerank", action="store_true", help="跳过重排（rerank 配额受限时）")
    parser.add_argument("--loop", action="store_true", help="对失败集开反思回环（真调 LLM，SC-002）")
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            run(args.dataset, args.tenant, args.output, args.limit, args.calibrate,
                args.no_rerank, args.loop)
        )
    )
