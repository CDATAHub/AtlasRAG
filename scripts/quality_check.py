#!/usr/bin/env python3
"""T044 质量抽检：从闭卷集抽 N 条 L0 问题走完整问答（真实 API），
输出延迟（SC-003：P95 ≤ 8s）与每条的问题/答案/引用清单，供人工核对引用
一致性（SC-006：一致率 100%）。

用法：python scripts/quality_check.py [--n 20] [--out /tmp/quality_sample.md]
"""

import argparse
import asyncio
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import get_settings  # noqa: E402
from src.data.db import build_sessionmaker  # noqa: E402
from src.services.answer import answer_stream  # noqa: E402
from src.services.clients.embedding import DashscopeEmbedding  # noqa: E402
from src.services.clients.llm import DashscopeLlm  # noqa: E402
from src.services.clients.rerank import DashscopeRerank  # noqa: E402


async def main(n: int, out: str) -> None:
    s = get_settings()
    factory = build_sessionmaker(s.database_url)
    emb = DashscopeEmbedding(s.llm_base_url, s.llm_api_key, s.embedding_model, s.embedding_dim)
    rr = DashscopeRerank(s.rerank_endpoint, s.llm_api_key, s.rerank_model)
    llm = DashscopeLlm(s.llm_base_url, s.llm_api_key, s.llm_model, s.llm_max_tokens)

    items = [
        json.loads(line)
        for line in Path("data/evals/golden_qa_kaihe.clean.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    l0 = [i for i in items if i.get("difficulty") == "L0"]
    sample = random.Random(42).sample(l0, min(n, len(l0)))

    lines = ["# T044 质量抽检（人工核对引用一致性）", ""]
    latencies, cited = [], 0
    async with factory() as session:
        for i, item in enumerate(sample, 1):
            t0 = time.monotonic()
            first = None
            answer, citations, done = [], [], {}
            agen = answer_stream(
                session, factory, ctx_tenant_id="tenant-001", question=item["question"],
                embedding=emb, reranker=rr, llm=llm,
                hybrid_top_k=s.hybrid_top_k, rerank_top_k=s.rerank_top_k,
                use_rerank=s.use_rerank,
                refusal_threshold=s.refusal_threshold, chain_timeout_s=s.chain_timeout_s,
            )
            async for ev, p in agen:
                if first is None:
                    first = time.monotonic() - t0
                if ev == "answer":
                    answer.append(p.get("delta", ""))
                elif ev == "citations":
                    citations = p.get("citations", [])
                elif ev == "done":
                    done = p
            total = time.monotonic() - t0
            latencies.append(total)
            if citations:
                cited += 1
            lines += [
                f"## {i}. {item['question']}",
                f"- 延迟：首事件 {first:.2f}s / 总 {total:.2f}s ｜ gold：{item.get('gold_answer', '')[:80]}",
                f"- 答案：{''.join(answer)}",
                f"- 引用：{[(c['n'], c['sec_no'], c['quote'][:60]) for c in citations]}",
                "",
            ]
            print(f"{i}/{len(sample)} 总 {total:.2f}s 首事件 {first:.2f}s 引用 {len(citations)}")

    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1] if latencies else 0
    lines += [
        "---",
        f"**P95 总延迟：{p95:.2f}s（SC-003 门槛 8s）** ｜ 引用覆盖率：{cited}/{len(sample)}（SC-004 要求 100%）",
    ]
    Path(out).write_text("\n".join(lines), encoding="utf-8")
    print(f"\nP95={p95:.2f}s 引用覆盖 {cited}/{len(sample)}，明细已写入 {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--out", default="/tmp/quality_sample.md")
    asyncio.run(main(parser.parse_args().n, parser.parse_args().out))
