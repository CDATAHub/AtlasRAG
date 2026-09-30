"""doc_reader 工具（章程 I；002 契约先行 → specs/003 US3 兑现实现）。

按 doc_id（全文分章）/ (doc_id, sec_no)（指定章节）读取条款原文；
强制租户过滤（与既有检索口径一致，clarify Q3；密级过滤随阶段 5 统一落地）。
不存在的文档/章节抛 ToolError → executor 结构化失败（permanent），由 reflect 决策。
"""

import uuid
from typing import ClassVar

from pydantic import BaseModel, Field
from sqlalchemy import select

from src.data.models import Chunk, Document
from src.tools.base import ToolContext, ToolError, ToolPolicy

SCOPE = "retrieval:read"


class DocReaderArgs(BaseModel):
    doc_id: str = Field(description="条款文档 ID")
    sec_no: str | None = Field(default=None, description="章节编号（如 2.3.1），缺省读全文大纲")


class DocReaderResult(BaseModel):
    doc_id: str
    title: str
    sections: list[dict]  # [{sec_no, title, text}]


class DocReaderTool:
    name: ClassVar[str] = "doc_reader"
    description: ClassVar[str] = "按文档 ID / 章节编号读取条款原文"
    scope: ClassVar[str] = SCOPE
    args_model: ClassVar[type[BaseModel]] = DocReaderArgs
    result_model: ClassVar[type[BaseModel]] = DocReaderResult
    policy = ToolPolicy(
        timeout_ms=5000, max_retries=1, idempotent=True, parallel_safe=True,
        required_scopes=("retrieval:read",),
    )

    async def invoke(self, ctx: ToolContext, args: DocReaderArgs) -> DocReaderResult:
        if ctx.session is None:
            raise ToolError("doc_reader requires a db session")
        try:
            doc_uuid = uuid.UUID(args.doc_id)
        except ValueError as exc:
            raise ToolError(f"invalid doc_id: {args.doc_id}") from exc

        doc = (
            await ctx.session.execute(
                select(Document).where(
                    Document.id == doc_uuid, Document.tenant_id == ctx.tenant_id
                )
            )
        ).scalar_one_or_none()
        if doc is None:  # 含跨租户：不区分「不存在」与「无权」，不泄漏存在性
            raise ToolError(f"document not found: {args.doc_id}")

        stmt = (
            select(Chunk)
            .where(
                Chunk.doc_id == doc_uuid,
                Chunk.tenant_id == ctx.tenant_id,
                Chunk.chunk_type == "parent",
            )
            .order_by(Chunk.sec_no.nulls_last())
        )
        if args.sec_no is not None:
            stmt = stmt.where(Chunk.sec_no == args.sec_no)
        rows = (await ctx.session.execute(stmt)).scalars().all()

        if args.sec_no is not None and not rows:
            raise ToolError(f"section not found: {args.doc_id}/{args.sec_no}")  # 不回退全文

        sections = [
            {
                "sec_no": row.sec_no,
                "title": (row.meta or {}).get("title") or f"{doc.title} {row.sec_no or ''}".strip(),
                "text": row.text,
            }
            for row in rows
        ]
        return DocReaderResult(doc_id=args.doc_id, title=doc.title, sections=sections)

    @staticmethod
    def hits_of(result: DocReaderResult) -> list[dict]:
        """证据形态（research D6）：章节 → evidence hit；score=0.0 仅占位，
        排序与拒答判定依赖同答内 hybrid 证据（见 generate._flatten_evidence）。"""
        return [
            {
                "n": i,
                "doc_id": result.doc_id,
                "title": sec["title"],
                "sec_no": sec["sec_no"],
                "score": 0.0,
                "parent_text": sec["text"],
            }
            for i, sec in enumerate(result.sections, start=1)
        ]
