"""混合检索（research D1/D3）：向量路 + 关键词路 → RRF 融合。

hybrid_search 保持纯函数签名（显式入参/出参、无全局状态）——
阶段 2 以 Tool Registry 包装时接口不变（原则 I 豁免的兑现前提）。
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.rag.tokens import tokenize

Hit = dict  # {doc_id, title, parent_id, parent_text, sec_no, text, rrf_score}

_K = 60  # RRF 常数（research D4）


def rrf_fusion(rank_lists: list[list[str]], k: int = _K) -> list[tuple[str, float]]:
    """纯函数：多路排名 → [(id, 原始 RRF 分)] 按分数降序（并列保持先出现者优先）。"""
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for ranks in rank_lists:
        for rank, item_id in enumerate(ranks):
            first_seen.setdefault(item_id, len(first_seen))
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    order = sorted(scores, key=lambda cid: (-scores[cid], first_seen[cid]))
    return [(cid, scores[cid]) for cid in order]


async def _set_iterative_scan(session: AsyncSession) -> None:
    """pgvector ≥0.8：HNSW 索引扫描后过滤导致结果不足 top_k 的官方对策（research D1）。"""
    await session.execute(text("SET hnsw.iterative_scan = relaxed_order"))


def _vec_literal(query_vec: list[float]) -> str:
    """pgvector 字面量（经 CAST 注入，asyncpg 类型推断友好）。"""
    return "[" + ",".join(f"{x:.7f}" for x in query_vec) + "]"


async def vector_search(
    session: AsyncSession, tenant_id: str, query_vec: list[float], limit: int
) -> list[str]:
    rows = await session.execute(
        text(
            "SELECT id FROM chunk "
            "WHERE tenant_id = :t AND chunk_type = 'child' AND embedding IS NOT NULL "
            "ORDER BY embedding <=> CAST(:vec AS vector) LIMIT :n"
        ),
        {"t": tenant_id, "vec": _vec_literal(query_vec), "n": limit * 3},  # 3× 超采样再截断
    )
    return [str(r[0]) for r in rows.all()][:limit]


async def keyword_search(
    session: AsyncSession, tenant_id: str, query: str, limit: int
) -> list[str]:
    tokens = tokenize(query)
    if not tokens:
        return []
    tsquery = " | ".join(f"'{tok.replace(chr(39), '')}'" for tok in tokens)
    rows = await session.execute(
        text(
            "SELECT id FROM chunk "
            "WHERE tenant_id = :t AND chunk_type = 'child' AND tsv @@ to_tsquery('simple', :q) "
            "ORDER BY ts_rank_cd(tsv, to_tsquery('simple', :q)) DESC LIMIT :n"
        ),
        {"t": tenant_id, "q": tsquery, "n": limit},
    )
    return [str(r[0]) for r in rows.all()]


async def hybrid_search(
    session: AsyncSession,
    tenant_id: str,
    query: str,
    query_vec: list[float],
    top_k: int = 50,
) -> list[Hit]:
    """双路召回 + RRF 融合 → 取 top_k 子块及其父块信息。"""
    await _set_iterative_scan(session)
    vec_ids = await vector_search(session, tenant_id, query_vec, top_k)
    kw_ids = await keyword_search(session, tenant_id, query, top_k)
    fused = rrf_fusion([vec_ids, kw_ids])[:top_k]
    if not fused:
        return []

    id_list = ", ".join(f"'{uuid.UUID(cid)}'::uuid" for cid, _ in fused)  # 已验证的 UUID
    rows = await session.execute(
        text(
            "SELECT c.id, c.sec_no, c.text, c.parent_id, p.text AS parent_text, "
            "       d.id AS doc_id, d.title "
            "FROM chunk c "
            "JOIN chunk p ON p.id = c.parent_id "
            "JOIN document d ON d.id = c.doc_id "
            f"WHERE c.tenant_id = :t AND c.id IN ({id_list})"
        ),
        {"t": tenant_id},
    )
    by_id = {str(r.id): dict(r._mapping) for r in rows.all()}
    return [
        {**by_id[cid], "rrf_score": round(score, 6)}
        for cid, score in fused
        if cid in by_id
    ]
