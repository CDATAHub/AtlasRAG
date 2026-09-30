"""零 LLM 端到端演示（specs/003 quickstart 验证 2~6；research D9）。

逐场景演示执行引擎管道行为，输出断言式结果（✓/✗），零真实外部服务（章程 VII）：

    uv run python scripts/demo_tool_engine.py --scenario pipeline   # 执行管道（SC-002/003/004）
    uv run python scripts/demo_tool_engine.py --scenario scopes     # 权限收敛（SC-005）
    uv run python scripts/demo_tool_engine.py --scenario parallel   # 一步多调用（SC-006）
    uv run python scripts/demo_tool_engine.py --scenario doc_reader # 章节精读（US3，需 PG+播种）
    uv run python scripts/demo_tool_engine.py --scenario mcp        # MCP 统一调度（SC-007）
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Settings  # noqa: E402
from src.tools.base import Registry, ToolContext, ToolPolicy  # noqa: E402
from src.tools.executor import ToolCall, ToolExecutor  # noqa: E402
from tests.unit.fakes import FakeTool  # noqa: E402

OK, FAIL = "  ✓", "  ✗"


def _settings(**kw) -> Settings:
    return Settings(tool_default_timeout_ms=300, tool_default_max_retries=1,
                    retry_backoff_base_s=0.01, **kw)


def _executor(*tools, **kw) -> tuple[ToolExecutor, ToolContext]:
    registry = Registry()
    for t in tools:
        registry.register(t)
    return ToolExecutor(registry, _settings(**kw)), ToolContext(
        tenant_id="demo", scopes=["retrieval:read", "misc:calc"]
    )


def _report(label: str, ok: bool, detail: str) -> bool:
    print(f"{OK if ok else FAIL} [{label}] {detail}")
    return ok


async def scenario_pipeline() -> bool:
    """超时/重试/非幂等/校验/恢复五条管道行为。"""
    all_ok = True
    _, ctx = _executor()

    tool = FakeTool(hang_s=5.0, policy=ToolPolicy(timeout_ms=200, max_retries=0))
    ex, ctx = _executor(tool)
    rec = await ex.execute(ToolCall("fake", "q", {"query": "q"}), ctx)
    all_ok &= _report("timeout", rec.error_class == "timeout",
                      f"挂起工具 200ms 上限内返回 error_class={rec.error_class}")

    tool = FakeTool(fail_first_n=1)
    ex, _ = _executor(tool)
    rec = await ex.execute(ToolCall("fake", "q", {"query": "q"}), ctx)
    all_ok &= _report("transient", rec.ok and rec.attempts == 2,
                      f"前败后成 attempts={rec.attempts} 重试成功")

    tool = FakeTool(fail_first_n=1, policy=ToolPolicy(timeout_ms=200, max_retries=3, idempotent=False))
    ex, _ = _executor(tool)
    rec = await ex.execute(ToolCall("fake", "q", {"query": "q"}), ctx)
    all_ok &= _report("non-idem", not rec.ok and rec.attempts == 1 and tool.invoke_count == 1,
                      "非幂等工具失败 attempts=1 零重发")

    tool = FakeTool()
    ex, _ = _executor(tool)
    rec = await ex.execute(ToolCall("fake", "q", {"query": ""}), ctx)
    all_ok &= _report("validate", rec.error_class == "validation" and tool.invoke_count == 0,
                      "非法参数进实现前拦截 error_class=validation")

    tool = FakeTool(exc=RuntimeError("boom"))
    ex, _ = _executor(tool)
    rec = await ex.execute(ToolCall("fake", "q", {"query": "q"}), ctx, trace_id="tr-demo")
    all_ok &= _report("recovered", rec.error_class == "unknown" and rec.trace_id == "tr-demo",
                      "未预期异常包装结构化记录（工具名/分类/耗时/trace_id），循环不中断")
    return all_ok


async def scenario_scopes() -> bool:
    """scopes 驱动工具面 + 越权拒绝。"""
    from tests.unit.test_registry_scopes import CalcScopedTool

    retrieval, calc = FakeTool(), CalcScopedTool()
    registry = Registry()
    registry.register(retrieval)
    registry.register(calc)

    for scopes, expect in ((["retrieval:read"], {"fake"}),
                           (["misc:calc"], {"mcp.calc"}),
                           (["retrieval:read", "misc:calc"], {"fake", "mcp.calc"}),
                           ([], set())):
        visible = {t["name"] for t in registry.visible_tools(scopes)}
        if not _report("scopes", visible == expect,
                       f"scopes={scopes} → 工具面 = {sorted(visible) or '[]'}"):
            return False

    ex = ToolExecutor(registry, _settings())
    rec = await ex.execute(ToolCall("fake", "q", {"query": "q"}),
                           ToolContext(tenant_id="demo", scopes=["misc:calc"]))
    return _report("permission", rec.error_class == "permission" and retrieval.invoke_count == 0,
                   "越权直调 → error_class=permission，实现未执行")


async def scenario_parallel() -> bool:
    """一步两调用并发 ≤1.5x + 部分失败保留。"""
    from tests.integration.test_parallel_step import ReaderFake

    a = FakeTool(hang_s=0.2, policy=ToolPolicy(timeout_ms=5000, max_retries=0))
    b = ReaderFake(hang_s=0.2)
    ex, ctx = _executor(a, b)
    calls = [ToolCall("fake", "q1", {"query": "q1"}),
             ToolCall("doc_reader", "q2", {"query": "q2"})]
    t0 = time.monotonic()
    records = await ex.execute_step(calls, ctx)
    elapsed = time.monotonic() - t0
    ok1 = _report("parallel", elapsed < 0.3 and [r.tool for r in records] == ["fake", "doc_reader"],
                  f"两调用（各 200ms）墙钟 {elapsed*1000:.0f}ms ≤1.5x，结果保序")

    b2 = ReaderFake(exc=RuntimeError("boom"))
    ex2, _ = _executor(FakeTool(hang_s=0.05), b2)
    records = await ex2.execute_step(calls, ctx)
    ok2 = _report("partial", [r.ok for r in records] == [True, False],
                  "其一失败 → 成功结果保留、失败单独 failed，不连带取消")
    return ok1 and ok2


async def scenario_doc_reader() -> bool:
    """播种库三场景（需本地 PG + 播种数据；CI 由 test_doc_reader.py 覆盖）。"""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine
    from src.config import get_settings
    from src.data.models import Document
    from src.tools.doc_reader import DocReaderArgs, DocReaderTool

    url = get_settings().database_url
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} [doc_reader] 本地开发库不可达（{url}）：{exc}")
        print("       提示：docker compose up -d 后重跑；CI 走 tests/integration/test_doc_reader.py")
        await engine.dispose()
        return False

    from src.data.db import build_sessionmaker
    factory = build_sessionmaker(url)
    tool = DocReaderTool()
    all_ok = True
    async with factory() as s:
        doc = (
            await s.execute(select(Document).limit(1))
        ).scalar_one_or_none()
        if doc is None:
            print(f"{FAIL} [doc_reader] 库无文档：先跑 scripts/import_corpus.py 播种")
            await engine.dispose()
            return False
        full = await tool.invoke(ToolContext(session=s, tenant_id=doc.tenant_id), DocReaderArgs(doc_id=str(doc.id)))
        all_ok &= _report("doc_reader", True, f"doc_id → {len(full.sections)} 章节（title={full.title[:20]}…）")
        if full.sections:
            target = full.sections[0]
            one = await tool.invoke(ToolContext(session=s, tenant_id=doc.tenant_id),
                                    DocReaderArgs(doc_id=str(doc.id), sec_no=target["sec_no"]))
            all_ok &= _report("section", len(one.sections) == 1, f"sec_no={target['sec_no']} → 指定章节原文")
    await engine.dispose()
    return all_ok


async def scenario_mcp() -> bool:
    """内存版演示 Server：清单注册 → 管道调用。stdio 全链路见 quickstart 验证 6。"""
    from tests.unit.test_mcp_client import _demo_server_session
    from src.tools.mcp_client import McpSource

    session, cleanup = await _demo_server_session()
    try:
        registry = Registry()
        source = McpSource(registry, _settings(mcp_tool_scopes=["misc:calc"]))
        registered = await source.register_from_session(session)
        ok = _report("register", sorted(registered) == ["mcp.premium_calc", "mcp.waiting_period"],
                     f"清单自动注册：{sorted(registered)}（inputSchema → args_model）")
        ex = ToolExecutor(registry, _settings(mcp_tool_scopes=["misc:calc"]))
        ctx = ToolContext(tenant_id="demo", scopes=["misc:calc"])
        rec = await ex.execute(ToolCall("mcp.waiting_period", "等待期", {"product": "重疾险A"}), ctx)
        ok &= _report("unified", rec.ok and "90日" in rec.result.text,
                      f"经同管道调用 mcp.waiting_period →「{rec.result.text}」（attempts={rec.attempts}）")
        return ok
    finally:
        await cleanup()


SCENARIOS = {
    "pipeline": scenario_pipeline,
    "scopes": scenario_scopes,
    "parallel": scenario_parallel,
    "doc_reader": scenario_doc_reader,
    "mcp": scenario_mcp,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="工具执行引擎零 LLM 演示")
    parser.add_argument("--scenario", choices=[*SCENARIOS, "all"], default="all")
    args = parser.parse_args()
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]

    all_ok = True
    for name in names:
        print(f"\n== {name} ==")
        all_ok &= asyncio.run(SCENARIOS[name]())
    print(f"\n结果：{'全部通过' if all_ok else '存在失败项'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
