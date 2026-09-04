"""hybrid_search 工具：包装阶段 1 检索纯函数（rag/hybrid + rag/rerank），签名不变。

阶段 1 T019 的设计承诺在此兑现：检索以工具身份进 Registry（章程 I），
plan/route 经 Registry 调度，模型可见面收敛为「条款检索」（clarify Q1）。
"""

from pydantic import BaseModel, Field

from src.rag.hybrid import hybrid_search as _hybrid_search
from src.rag.rerank import rerank_hits as _rerank_hits
from src.services.clients.embedding import EmbeddingClient
from src.services.clients.rerank import RerankClient
from src.tools.base import ToolContext, ToolError

SCOPE = "retrieval:read"


class HybridSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200, description="改写后的检索式")


class HitItem(BaseModel):
    n: int
    doc_id: str
    title: str
    sec_no: str | None
    score: float
    parent_text: str


class HybridSearchResult(BaseModel):
    hits: list[HitItem]
    top_score: float | None


class HybridSearchTool:
    name = "hybrid_search"
    description = "在租户条款库中混合检索（向量+关键词融合+重排），返回最相关条款父块"
    scope = SCOPE
    args_model = HybridSearchArgs
    result_model = HybridSearchResult

    def __init__(
        self,
        embedding: EmbeddingClient,
        reranker: RerankClient,
        *,
        hybrid_top_k: int = 50,
        rerank_top_k: int = 5,
        use_rerank: bool = True,
    ):
        self._embedding = embedding
        self._reranker = reranker
        self._hybrid_top_k = hybrid_top_k
        self._rerank_top_k = rerank_top_k
        self._use_rerank = use_rerank

    async def invoke(self, ctx: ToolContext, args: HybridSearchArgs) -> HybridSearchResult:
        try:
            query_vec = (await self._embedding.embed([args.query]))[0]
            hits = await _hybrid_search(
                ctx.session, ctx.tenant_id, args.query, query_vec, self._hybrid_top_k
            )
            if self._use_rerank:
                ranked = await _rerank_hits(self._reranker, args.query, hits, self._rerank_top_k)
            else:
                ranked = hits[: self._rerank_top_k]
                for hit in ranked:
                    hit["score"] = float(hit.get("rrf_score", 0.0))
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 工具故障统一 ToolError，由 reflect 决策
            raise ToolError(f"hybrid_search failed: {exc}") from exc

        items = [
            HitItem(
                n=n,
                doc_id=str(hit["doc_id"]),
                title=hit["title"],
                sec_no=hit.get("sec_no"),
                score=round(float(hit.get("score", hit.get("rrf_score", 0.0))), 4),
                parent_text=hit["parent_text"],
            )
            for n, hit in enumerate(ranked, start=1)
        ]
        return HybridSearchResult(
            hits=items, top_score=items[0].score if items else None
        )
