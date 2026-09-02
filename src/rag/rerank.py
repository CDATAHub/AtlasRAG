"""重排与拒答信号（research D4）：rerank 分数跨查询可比，阈值设在重排分上。"""

from src.rag.hybrid import Hit
from src.services.clients.rerank import RerankClient


async def rerank_hits(
    reranker: RerankClient,
    query: str,
    hits: list[Hit],
    top_k: int = 5,
) -> list[Hit]:
    """精排取 top_k；每个 Hit 增写 rerank 分数（score），降序。零候选直接返回。"""
    if not hits:
        return []
    scores = await reranker.rerank(query, [h["parent_text"] for h in hits])
    ranked = sorted(
        ({**hit, "score": float(score)} for hit, score in zip(hits, scores, strict=True)),
        key=lambda h: h["score"],
        reverse=True,
    )
    return ranked[:top_k]
