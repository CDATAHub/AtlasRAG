"""入库编排（research D10）：解析 → 切分 → Embedding → 索引 → 状态机。

processing → indexed | failed；failed 记录原因，reprocess 重跑（同 version 覆盖）。
后台执行用独立会话工厂（asyncio.create_task，阶段 1 不引入消息队列）。
"""

import traceback
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from src.data import dao
from src.rag.chunker import split_sections
from src.rag.indexer import index_document
from src.rag.parser import parse_document
from src.services.clients.embedding import EmbeddingClient


async def run_ingestion(
    session_factory: async_sessionmaker,
    embedding: EmbeddingClient,
    doc_id: uuid.UUID,
    tenant_id: str,
) -> None:
    async with session_factory() as session:
        doc = await dao.get_document(session, tenant_id, doc_id)
        if doc is None:
            return
        try:
            sections = parse_document(doc.raw_text)
            parents, children = split_sections(sections)
            await index_document(session, embedding, tenant_id, doc.id, parents, children)
            doc.status = "indexed"
            doc.error = None
        except Exception as exc:  # noqa: BLE001 —— 单文档失败不影响其他文档（FR-002/US2）
            doc.status = "failed"
            doc.error = f"{type(exc).__name__}: {exc}"[:500]
            traceback.print_exc()
        await session.commit()
