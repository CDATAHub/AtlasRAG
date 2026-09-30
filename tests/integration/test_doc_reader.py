"""doc_reader 集成测试（specs/003 US3；FR-012）：播种库三场景 + 租户隔离。

章程 VII：PG 为测试基座（不可达自动 skip），无真实外部服务。
"""

import uuid

import pytest
from sqlalchemy import select

from src.data.models import Document
from src.tools.base import ToolContext, ToolError
from src.tools.doc_reader import DocReaderArgs, DocReaderTool
from src.tools.executor import ToolCall, ToolExecutor

from tests.conftest import OTHER_TENANT, TENANT


async def _waiting_doc_id(db) -> str:
    async with db() as s:
        row = (
            await s.execute(
                select(Document).where(
                    Document.tenant_id == TENANT, Document.title.like("康护%")
                )
            )
        ).scalar_one()
        return str(row.id)


async def test_read_full_document_sections(seeded_lib, db):
    """doc_id → 全部章节，sec_no 有序，text 为播种原文（US3 场景 1）。"""
    doc_id = await _waiting_doc_id(db)
    tool = DocReaderTool()
    async with db() as s:
        result = await tool.invoke(
            ToolContext(session=s, tenant_id=TENANT, scopes=["retrieval:read"]),
            DocReaderArgs(doc_id=doc_id),
        )
    assert result.doc_id == doc_id
    assert result.title.startswith("康护")
    sec_nos = [sec["sec_no"] for sec in result.sections]
    assert sec_nos == sorted(sec_nos, key=lambda x: (x is None, x or ""))
    assert any("90 日" in sec["text"] for sec in result.sections)
    # 证据形态（进 evidence 的 hits，research D6：score=0.0 仅作排序占位）
    hits = tool.hits_of(result)
    assert all(h["doc_id"] == doc_id and h["parent_text"] for h in hits)


async def test_read_single_section(seeded_lib, db):
    """(doc_id, sec_no) → 仅指定章节原文（US3 场景 2）。"""
    doc_id = await _waiting_doc_id(db)
    tool = DocReaderTool()
    async with db() as s:
        full = await tool.invoke(
            ToolContext(session=s, tenant_id=TENANT, scopes=["retrieval:read"]),
            DocReaderArgs(doc_id=doc_id),
        )
        target = next(sec for sec in full.sections if sec["sec_no"] and "2" in sec["sec_no"])
        one = await tool.invoke(
            ToolContext(session=s, tenant_id=TENANT, scopes=["retrieval:read"]),
            DocReaderArgs(doc_id=doc_id, sec_no=target["sec_no"]),
        )
    assert [sec["sec_no"] for sec in one.sections] == [target["sec_no"]]
    assert one.sections[0]["text"] == target["text"]


async def test_cross_tenant_not_found(seeded_lib, db):
    """跨租户 doc_id → not_found（US2/US3 场景 3：不泄漏其他租户内容）。"""
    doc_id = await _waiting_doc_id(db)
    tool = DocReaderTool()
    async with db() as s:
        with pytest.raises(ToolError, match="not found"):
            await tool.invoke(
                ToolContext(session=s, tenant_id=OTHER_TENANT, scopes=["retrieval:read"]),
                DocReaderArgs(doc_id=doc_id),
            )


async def test_section_not_found_no_fallback(seeded_lib, db):
    """sec_no 不存在 → section_not_found，不回退全文（FR-012/Edge）。"""
    doc_id = await _waiting_doc_id(db)
    tool = DocReaderTool()
    async with db() as s:
        with pytest.raises(ToolError, match="section"):
            await tool.invoke(
                ToolContext(session=s, tenant_id=TENANT, scopes=["retrieval:read"]),
                DocReaderArgs(doc_id=doc_id, sec_no="9.9.9"),
            )


async def test_executor_wraps_doc_reader(seeded_lib, db):
    """经 executor 同管道调度：成功进 evidence 的 hits 形态；失败结构化（US1 管道对新工具通用）。"""
    from src.config import Settings
    from src.tools.base import Registry

    doc_id = await _waiting_doc_id(db)
    registry = Registry()
    registry.register(DocReaderTool())
    executor = ToolExecutor(registry, Settings(tool_default_timeout_ms=5000))
    async with db() as s:
        ctx = ToolContext(session=s, tenant_id=TENANT, scopes=["retrieval:read"])
        rec = await executor.execute(
            ToolCall(tool="doc_reader", query=doc_id, args={"doc_id": doc_id}), ctx
        )
    assert rec.ok is True
    assert rec.hits and rec.hits[0]["doc_id"] == doc_id
    assert rec.hits[0]["parent_text"]  # 章节原文进入证据形态

    async with db() as s:
        ctx = ToolContext(session=s, tenant_id=TENANT, scopes=[])  # 越权：空权限域
        rec = await executor.execute(
            ToolCall(tool="doc_reader", query=doc_id, args={"doc_id": doc_id}), ctx
        )
    assert rec.ok is False and rec.error_class == "permission"
