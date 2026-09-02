"""ORM 三表：document / chunk / runtime_log（specs/001-single-chain-rag/data-model.md）。

合规三字段 visibility/region/expire_at 与 version/tenant_id 从第一版即预留（章程 V）。
raw_text 为本阶段对 data-model 的一处补充：对象存储属阶段 5，重试（reprocess）
需要原始文本，暂存库内。
"""

import datetime
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 1024  # 与 Settings.embedding_dim 保持一致（research D2）


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (
        CheckConstraint("status in ('processing','indexed','failed')", name="ck_doc_status"),
        CheckConstraint("visibility in ('public','internal','confidential')", name="ck_doc_vis"),
        Index("ix_document_tenant_hash", "tenant_id", "content_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, default="upload")
    content_hash: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    visibility: Mapped[str] = mapped_column(Text, default="internal")
    region: Mapped[str] = mapped_column(Text, default="cn")
    expire_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, default="processing")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Chunk(Base):
    __tablename__ = "chunk"
    __table_args__ = (
        CheckConstraint("chunk_type in ('parent','child')", name="ck_chunk_type"),
        Index("ix_chunk_tenant_doc", "tenant_id", "doc_id"),
        Index("ix_chunk_parent", "parent_id"),
        Index("ix_chunk_tsv", "tsv", postgresql_using="gin"),
        # HNSW（research D1）：cosine 距离 + 迭代扫描参数
        Index(
            "ix_chunk_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doc_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(Text)
    chunk_type: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunk.id", ondelete="CASCADE"), nullable=True
    )
    sec_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    tsv: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RuntimeLog(Base):
    __tablename__ = "runtime_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(Text, index=True)
    tenant_id: Mapped[str] = mapped_column(Text)
    question: Mapped[str] = mapped_column(Text)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    top_score: Mapped[float | None] = mapped_column(nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    refused: Mapped[bool] = mapped_column(Boolean, default=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
