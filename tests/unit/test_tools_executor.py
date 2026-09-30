"""executor 执行引擎单测（specs/003 US1；SC-002/003/004/008 的单测层）。

FakeTool 可编程故障驱动逐中间件断言（章程 VII：零真实外部服务）。
"""

import time

import pytest

from src.config import Settings
from src.tools.base import Registry, ToolContext
from src.tools.executor import ToolCall, ToolExecutor

from tests.unit.fakes import FakeTool, FakeToolArgs


def _executor(policy_tool: FakeTool | None = None) -> tuple[ToolExecutor, ToolContext]:
    registry = Registry()
    registry.register(policy_tool or FakeTool())
    settings = Settings(
        tool_default_timeout_ms=200, tool_default_max_retries=1, retry_backoff_base_s=0.01
    )
    ctx = ToolContext(session=None, tenant_id="t1", scopes=["retrieval:read"])
    return ToolExecutor(registry, settings), ctx


async def test_timeout_returns_within_limit():
    """SC-002：挂起工具在契约 timeout_ms 内返回 error_class=timeout，不无限等待。"""
    from src.tools.base import ToolPolicy

    tool = FakeTool(hang_s=10.0, policy=ToolPolicy(timeout_ms=200, max_retries=0))
    executor, ctx = _executor(tool)
    t0 = time.monotonic()
    rec = await executor.execute(ToolCall(tool="fake", query="q", args={"query": "q"}), ctx)
    elapsed = time.monotonic() - t0
    assert rec.ok is False
    assert rec.error_class == "timeout"
    assert elapsed < 2.0  # 200ms 上限 + 重试为 0：远小于挂起时长即视为按时返回
    assert rec.attempts == 1


async def test_transient_retry_succeeds():
    """SC-003：首败后成的瞬时故障经退避重试成功，attempts=2。"""
    tool = FakeTool(fail_first_n=1)
    executor, ctx = _executor(tool)
    rec = await executor.execute(ToolCall(tool="fake", query="q", args={"query": "q"}), ctx)
    assert rec.ok is True
    assert rec.attempts == 2
    assert tool.invoke_count == 2


async def test_non_idempotent_never_retries():
    """SC-004：非幂等工具失败零重发，attempts=1（FR-008）。"""
    from src.tools.base import ToolPolicy

    tool = FakeTool(fail_first_n=1, policy=ToolPolicy(timeout_ms=200, max_retries=3, idempotent=False))
    executor, ctx = _executor(tool)
    rec = await executor.execute(ToolCall(tool="fake", query="q", args={"query": "q"}), ctx)
    assert rec.ok is False
    assert rec.attempts == 1
    assert tool.invoke_count == 1


async def test_validation_intercepted_before_invoke():
    """FR-005：非法参数进实现前拦截，error_class=validation，实现未执行。"""
    tool = FakeTool()
    executor, ctx = _executor(tool)
    rec = await executor.execute(ToolCall(tool="fake", query="q", args={"query": ""}), ctx)
    assert rec.ok is False
    assert rec.error_class == "validation"
    assert tool.invoke_count == 0
    assert rec.attempts == 1


async def test_unknown_tool_structured_failure():
    """未知工具名：结构化失败（002 口径 unknown tool），不抛出。"""
    executor, ctx = _executor()
    rec = await executor.execute(ToolCall(tool="nope", query="q", args={}), ctx)
    assert rec.ok is False
    assert rec.error_class == "permanent"
    assert "unknown tool" in (rec.error or "")


async def test_unexpected_exception_wrapped():
    """FR-010：未预期异常包装为结构化记录（工具名/分类/耗时/trace_id），不抛出。"""
    tool = FakeTool(exc=RuntimeError("boom"))
    executor, ctx = _executor(tool)
    rec = await executor.execute(
        ToolCall(tool="fake", query="q", args={"query": "q"}), ctx, trace_id="tr-1"
    )
    assert rec.ok is False
    assert rec.error_class == "unknown"
    assert rec.tool == "fake"
    assert "boom" in (rec.error or "")
    assert rec.duration_ms >= 0
    assert rec.trace_id == "tr-1"
    assert rec.attempts == 1  # unknown 不可重试（FR-007）


async def test_permission_denied_before_invoke():
    """SC-005（单测层）：越权调用执行前被拒，实现未执行。"""
    tool = FakeTool()
    registry = Registry()
    registry.register(tool)
    settings = Settings(tool_default_timeout_ms=200, tool_default_max_retries=1)
    executor = ToolExecutor(registry, settings)
    ctx = ToolContext(session=None, tenant_id="t1", scopes=[])  # 无任何权限域
    rec = await executor.execute(ToolCall(tool="fake", query="q", args={"query": "q"}), ctx)
    assert rec.ok is False
    assert rec.error_class == "permission"
    assert tool.invoke_count == 0


async def test_wrapped_equals_direct_invocation():
    """SC-001（单测层）：executor 包装对任意工具是透传——同 ctx/args 结果与直调一致。

    hybrid_search 的 SQL 级透传断言属集成层（需 PG），见 quickstart 验证 1 既有回归。
    """
    from src.tools.hybrid_search import HitItem, HybridSearchResult

    expected = HybridSearchResult(
        hits=[HitItem(n=1, doc_id="d1", title="条款", sec_no="2.3", score=0.9, parent_text="原文")],
        top_score=0.9,
    )
    tool = FakeTool(result=expected)
    executor, ctx = _executor(tool)
    rec = await executor.execute(ToolCall(tool="fake", query="q", args={"query": "q"}), ctx)
    assert rec.ok is True
    assert rec.result is expected
    assert [h.model_dump() for h in rec.result.hits] == [h.model_dump() for h in expected.hits]


async def test_retry_exhaustion_reports_last_error():
    """重试耗尽：与单次失败同构（结构化 failed 记录，attempts=1+max_retries）。"""
    tool = FakeTool(fail_first_n=99)  # 永远瞬时失败
    executor, ctx = _executor(tool)
    rec = await executor.execute(ToolCall(tool="fake", query="q", args={"query": "q"}), ctx)
    assert rec.ok is False
    assert rec.error_class == "transient"
    assert rec.attempts == 2  # max_retries=1
    assert tool.invoke_count == 2


def test_args_model_importable():
    """FakeTool 契约自洽（章程 VII mock 以契约为基准）。"""
    assert FakeToolArgs.model_validate({"query": "q"}).query == "q"
