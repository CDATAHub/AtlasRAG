"""租户过滤数据访问层（章程 V）：所有查询强制 tenant_id，业务代码不得绕过。"""

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models import Chunk, Document, RuntimeLog


async def find_by_hash(session: AsyncSession, tenant_id: str, content_hash: str) -> Document | None:
    """同租户下同内容指纹的既有文档（同版本归并依据，clarify Q2）。"""
    stmt = (
        select(Document)
        .where(Document.tenant_id == tenant_id, Document.content_hash == content_hash)
        .limit(1)
    )
    return (await session.scalars(stmt)).first()


async def next_version(session: AsyncSession, tenant_id: str, title: str) -> int:
    """同 title 下版本号递增（data-model：version 同 title 递增）。"""
    stmt = select(func.max(Document.version)).where(
        Document.tenant_id == tenant_id, Document.title == title
    )
    current = (await session.scalars(stmt)).first() or 0
    return int(current) + 1


async def get_document(session: AsyncSession, tenant_id: str, doc_id: uuid.UUID) -> Document | None:
    """他租户的 doc_id 一律视为不存在（不泄露存在性）。"""
    stmt = select(Document).where(Document.id == doc_id, Document.tenant_id == tenant_id)
    return (await session.scalars(stmt)).first()


async def add_document(session: AsyncSession, doc: Document) -> Document:
    session.add(doc)
    await session.flush()
    return doc


async def add_chunks(session: AsyncSession, chunks: list[Chunk]) -> None:
    session.add_all(chunks)
    await session.flush()


async def delete_doc_chunks(session: AsyncSession, tenant_id: str, doc_id: uuid.UUID) -> None:
    """版本覆盖 / 重试前清空旧块（幂等，FR-003）。"""
    await session.execute(
        delete(Chunk).where(Chunk.tenant_id == tenant_id, Chunk.doc_id == doc_id)
    )


async def count_blocks(session: AsyncSession, tenant_id: str, doc_id: uuid.UUID) -> dict:
    stmt = (
        select(Chunk.chunk_type, func.count())
        .where(Chunk.tenant_id == tenant_id, Chunk.doc_id == doc_id)
        .group_by(Chunk.chunk_type)
    )
    counts = {"parent": 0, "child": 0}
    for chunk_type, n in (await session.execute(stmt)).all():
        counts[chunk_type] = int(n)
    return counts


async def count_children(session: AsyncSession, tenant_id: str) -> int:
    stmt = select(func.count()).where(Chunk.tenant_id == tenant_id, Chunk.chunk_type == "child")
    return int((await session.scalars(stmt)).first() or 0)


async def add_runtime_log(session: AsyncSession, log: RuntimeLog) -> None:
    session.add(log)
