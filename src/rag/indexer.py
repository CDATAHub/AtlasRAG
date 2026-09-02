"""索引写入（research D2/D3）：子块向量（批量 Embedding）+ jieba 预分词 tsvector。

tsvector 由数据库 to_tsvector('simple', 预分词文本) 计算（ADR-008，零扩展依赖）。
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.dao import delete_doc_chunks
from src.data.models import Chunk
from src.rag.chunker import ChildBlock, ParentBlock
from src.rag.tokens import segment
from src.services.clients.embedding import EmbeddingClient


async def index_document(
    session: AsyncSession,
    embedding: EmbeddingClient,
    tenant_id: str,
    doc_id: uuid.UUID,
    parents: list[ParentBlock],
    children: list[ChildBlock],
) -> int:
    """写入父子块；返回子块数。重试/版本覆盖前清空旧块（幂等）。"""
    await delete_doc_chunks(session, tenant_id, doc_id)

    parent_ids: dict[str, uuid.UUID] = {}
    for parent in parents:
        pid = uuid.uuid4()
        parent_ids[parent.key] = pid
        session.add(
            Chunk(
                id=pid,
                doc_id=doc_id,
                tenant_id=tenant_id,
                chunk_type="parent",
                parent_id=None,
                sec_no=parent.sec_no,
                text=parent.text,
            )
        )
    await session.flush()

    if children:
        vectors = await embedding.embed([child.text for child in children])
    else:
        vectors = []

    rows = []
    for child, vec in zip(children, vectors, strict=True):
        cid = uuid.uuid4()
        session.add(
            Chunk(
                id=cid,
                doc_id=doc_id,
                tenant_id=tenant_id,
                chunk_type="child",
                parent_id=parent_ids[child.parent_key],
                sec_no=child.sec_no,
                text=child.text,
                embedding=vec,
                meta={"position": child.position, "table_row": child.is_table_row},
            )
        )
        rows.append({"id": str(cid), "seg": segment(child.text)})
    await session.flush()

    if rows:  # tsv 在 SQL 侧由预分词文本计算（GIN 索引命中）
        await session.execute(
            text("UPDATE chunk SET tsv = to_tsvector('simple', :seg) WHERE id = CAST(:id AS uuid)"),
            rows,
        )
    return len(children)
