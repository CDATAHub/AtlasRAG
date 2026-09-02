"""问答编排（US1/US3）：检索 → 重排 → 拒答判定 → 带引用流式生成 → 运行档案。

产出 (event, payload) 序列，由路由层编码为 SSE（contracts/api.md §3）。
链路熔断 20s（章程 IV）：超时在流内发 refused done，不断连。
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from src.rag.hybrid import hybrid_search
from src.rag.rerank import rerank_hits
from src.services.clients.embedding import EmbeddingClient
from src.services.clients.llm import LlmClient
from src.services.clients.rerank import RerankClient
from src.services.citations import build_citations
from src.services.runtime_log import write_log

REFUSAL_TEXT = (
    "未在当前条款库中找到与该问题直接相关的条款。为避免误导，不作推测。"
    "建议补充险种名称或条款术语——例如「等待期」「宽限期」「现金价值」——再试一次。"
)

_SYSTEM_PROMPT = (
    "你是保险条款问答助手。只依据提供的资料回答，资料不足时明确说「资料不足」，"
    "禁止编造。回答简洁：先给直接结论，再附条款依据；不要复述问题、不要展开无关内容。"
    "每个结论后标注引用编号，格式如 [1]、[2]。"
)

TIMEOUT_REASON = "timeout"


def should_refuse(ranked: list[dict], threshold: float) -> bool:
    """拒答判定（FR-008 / research D4）：零命中或 top 重排分低于阈值。纯函数。"""
    return not ranked or float(ranked[0]["score"]) < threshold


class RetrievalUnavailable(Exception):
    """检索能力不可用（Embedding/检索层故障）→ 路由层转 503（FR-010）。"""


async def answer_stream(
    session: AsyncSession,
    log_session_factory,
    *,
    ctx_tenant_id: str,
    question: str,
    embedding: EmbeddingClient,
    reranker: RerankClient,
    llm: LlmClient,
    hybrid_top_k: int,
    rerank_top_k: int,
    use_rerank: bool = True,
    refusal_threshold: float = 0.35,
    chain_timeout_s: float = 20.0,
) -> AsyncIterator[tuple[str, dict]]:
    trace_id = f"tr-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    answer_parts: list[str] = []
    hit_count = 0
    top_score: float | None = None
    refused = False
    reason: str | None = None

    try:
        async with asyncio.timeout(chain_timeout_s):
            try:
                query_vec = (await embedding.embed([question]))[0]
                hits = await hybrid_search(session, ctx_tenant_id, question, query_vec, hybrid_top_k)
            except RetrievalUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 —— 检索层故障统一 503（FR-010）
                raise RetrievalUnavailable(str(exc)) from exc

            if use_rerank:
                ranked = await rerank_hits(reranker, question, hits, rerank_top_k)
                threshold = refusal_threshold  # 语义：重排相关性分（0~1）
            else:
                # 跳过重排：按 RRF 原始分排序；RRF 分无相关性语义 → 仅零命中拒答
                ranked = hits[:rerank_top_k]
                for hit in ranked:
                    hit["score"] = float(hit.get("rrf_score", 0.0))
                threshold = 0.0
            for n, hit in enumerate(ranked, start=1):
                hit["n"] = n
            hit_count = len(ranked)
            top_score = float(ranked[0]["score"]) if ranked else None

            if should_refuse(ranked, threshold):  # FR-008 拒答路径
                refused = True
                yield "answer", {"delta": REFUSAL_TEXT}
                yield "citations", {"citations": []}
                yield "done", _done(trace_id, started, refused, hit_count, top_score, reason)
                return

            yield "evidence", {
                "trace_id": trace_id,
                "hits": [
                    {
                        "n": hit["n"],
                        "doc_id": str(hit["doc_id"]),
                        "title": hit["title"],
                        "sec_no": hit.get("sec_no"),
                        "score": round(float(hit["score"]), 4),
                    }
                    for hit in ranked
                ],
            }

            # 生成上下文裁剪：top-3 父块、各截 1200 字——控制 prompt 规模保住首 token 时延
            # （citations 的 quote 截取仍用完整 parent_text，溯源不受影响）
            evidence_text = "\n\n".join(
                f"[{hit['n']}] {hit['title']} {hit.get('sec_no') or ''}\n{hit['parent_text'][:1200]}"
                for hit in ranked[:3]
            )
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"问题：{question}\n\n资料：\n{evidence_text}"},
            ]
            try:
                async for delta in llm.stream_chat(messages):
                    answer_parts.append(delta)
                    yield "answer", {"delta": delta}
            except Exception:  # noqa: BLE001 —— 生成故障也必须收敛（章程 IV）
                refused = True
                reason = "generate_failed"
                yield "done", _done(trace_id, started, refused, hit_count, top_score, reason)
                return
            answer_text = "".join(answer_parts)
            citations = build_citations(answer_text, ranked, question)
            yield "citations", {"citations": citations}
            yield "done", _done(trace_id, started, refused, hit_count, top_score, reason)
    except TimeoutError:
        refused = True
        reason = TIMEOUT_REASON
        yield "done", _done(trace_id, started, refused, hit_count, top_score, reason)
    finally:
        await write_log(
            log_session_factory,
            trace_id=trace_id,
            tenant_id=ctx_tenant_id,
            question=question,
            hit_count=hit_count,
            top_score=top_score,
            latency_ms=int((time.monotonic() - started) * 1000),
            refused=refused,
            answer="".join(answer_parts) or None,
        )


def _done(
    trace_id: str,
    started: float,
    refused: bool,
    hit_count: int,
    top_score: float | None,
    reason: str | None,
) -> dict:
    payload = {
        "trace_id": trace_id,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "refused": refused,
        "hit_count": hit_count,
        "top_score": round(top_score, 4) if top_score is not None else None,
    }
    if reason:
        payload["reason"] = reason
    return payload
