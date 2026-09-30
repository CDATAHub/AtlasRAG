"""一步多调用并行集成测试（specs/003 US4；SC-006/SC-008，clarify Q1）。

executor.execute_step：全 parallel_safe → gather 保序合并；含非 parallel_safe → 串行；
部分失败不连带取消（FR-011/Edge）。章程 VII：FakeTool 零真实外部服务。
"""

import time

from src.config import Settings
from src.tools.base import Registry, ToolContext, ToolPolicy
from src.tools.executor import ToolCall, ToolExecutor

from tests.unit.fakes import FakeTool, FakeToolArgs


class ReaderFake(FakeTool):
    """doc_reader 形态的第二工具（同 query 参数契约，模拟一步多调用混合调用面）。"""

    name = "doc_reader"
    args_model = FakeToolArgs
    policy = ToolPolicy(timeout_ms=5000, max_retries=0)


def _executor(*tools: FakeTool) -> tuple[ToolExecutor, ToolContext]:
    registry = Registry()
    for t in tools:
        registry.register(t)
    executor = ToolExecutor(
        registry,
        Settings(
            tool_default_timeout_ms=5000,
            tool_default_max_retries=0,
            retry_backoff_base_s=0.01,
        ),
    )
    ctx = ToolContext(session=None, tenant_id="t1", scopes=["retrieval:read", "misc:calc"])
    return executor, ctx


def _calls() -> list[ToolCall]:
    return [
        ToolCall(tool="fake", query="q1", args={"query": "q1"}),
        ToolCall(tool="doc_reader", query="q2", args={"query": "q2"}),
    ]


async def test_parallel_calls_within_1_5x():
    """SC-006：一步两独立调用并发执行，墙钟 ≤ 单调用 1.5x（200ms sleep → <300ms）。"""
    a = FakeTool(hang_s=0.2, policy=ToolPolicy(timeout_ms=5000, max_retries=0))
    b = ReaderFake(hang_s=0.2)
    executor, ctx = _executor(a, b)
    t0 = time.monotonic()
    records = await executor.execute_step(_calls(), ctx)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.3, f"parallel wall time {elapsed:.2f}s exceeds 1.5x"
    assert [r.tool for r in records] == ["fake", "doc_reader"]  # 保序
    assert all(r.ok for r in records)


async def test_non_parallel_safe_serializes():
    """FR-011：含非 parallel_safe 调用 → 整步串行（墙钟 ≥ 两者之和）。"""
    a = FakeTool(hang_s=0.15)
    b = ReaderFake(hang_s=0.15)
    b.policy = ToolPolicy(timeout_ms=5000, max_retries=0, parallel_safe=False)
    executor, ctx = _executor(a, b)
    t0 = time.monotonic()
    records = await executor.execute_step(_calls(), ctx)
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.3  # 串行 = 150ms + 150ms
    assert all(r.ok for r in records)


async def test_partial_failure_keeps_success():
    """FR-011/Edge：其一失败 → 成功结果保留，失败单独 failed，不连带取消。"""
    a = FakeTool(hang_s=0.05)
    b = ReaderFake(exc=RuntimeError("boom"))
    executor, ctx = _executor(a, b)
    records = await executor.execute_step(_calls(), ctx)
    assert [r.ok for r in records] == [True, False]
    assert records[1].error_class == "unknown"
    assert records[0].hits == [] or records[0].result is not None  # 成功侧负载在


async def test_step_records_carry_step_and_duration():
    """execute_step 结果携带 step 序号与耗时（FR-010 携带项，SSE 可观测）。"""
    a = FakeTool(hang_s=0.05)
    b = ReaderFake(hang_s=0.05)
    executor, ctx = _executor(a, b)
    records = await executor.execute_step(_calls(), ctx, step=3, trace_id="tr-x")
    assert all(r.step == 3 and r.trace_id == "tr-x" for r in records)
    assert all(r.duration_ms >= 0 for r in records)
