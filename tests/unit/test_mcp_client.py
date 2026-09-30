"""MCP 客户端单测（specs/003 US5；FR-002/013/014/015）。

mcp SDK in-memory transport（research D7/D9）：无子进程、无真实 IO（章程 VII）。
"""

import asyncio
import contextlib

import pytest

from src.config import Settings
from src.tools.base import Registry, ToolContext
from src.tools.executor import ToolCall, ToolExecutor


async def _demo_server_session():
    """内存版演示 Server：MCPServer + 两个计算工具，经 memory streams 连出 ClientSession。"""
    from mcp.client.session import ClientSession
    from mcp.server.lowlevel.server import Server as LowlevelServer
    from mcp.server.mcpserver import MCPServer
    from mcp.shared.memory import create_client_server_memory_streams
    from mcp import types

    server = MCPServer("atlas-demo")

    @server.tool()
    def premium_calc(age: int, sum_insured: float) -> float:
        """保费试算演示。"""
        return sum_insured * (1.0 + age / 1000.0)

    @server.tool()
    def waiting_period(product: str) -> str:
        """等待期查询演示。"""
        return f"{product}: 90日" if "重疾" in product else f"{product}: 30日"

    streams_cm = create_client_server_memory_streams()
    client_streams, server_streams = await streams_cm.__aenter__()
    cr, cw = client_streams
    sr, sw = server_streams

    lowlevel: LowlevelServer = server._lowlevel_server  # 2.x 内存运行的现实约束（非公开 API）
    task = asyncio.create_task(
        lowlevel.run(sr, sw, lowlevel.create_initialization_options())
    )

    session = ClientSession(cr, cw)
    await session.__aenter__()
    await session.initialize()

    async def _cleanup():
        task.cancel()  # 先停 server 任务（其 cancel scope 持有 server 侧流）
        with contextlib.suppress(asyncio.CancelledError, RuntimeError):
            await task
        with contextlib.suppress(RuntimeError):
            await session.__aexit__(None, None, None)
        with contextlib.suppress(RuntimeError):
            await streams_cm.__aexit__(None, None, None)

    return session, _cleanup


@pytest.fixture
async def demo_session():
    session, cleanup = await _demo_server_session()
    try:
        yield session
    finally:
        await cleanup()


def _settings() -> Settings:
    return Settings(mcp_tool_scopes=["misc:calc"], tool_default_timeout_ms=2000)


async def test_list_tools_dynamic_registration(demo_session):
    """FR-013：list_tools → mcp.<name> 动态注册，args_model 由 inputSchema 生成。"""
    from src.tools.mcp_client import McpSource

    registry = Registry()
    source = McpSource(registry, _settings())
    registered = await source.register_from_session(demo_session)

    assert sorted(registered) == ["mcp.premium_calc", "mcp.waiting_period"]
    tool = registry.get("mcp.premium_calc")
    assert tool is not None
    assert sorted(tool.args_model.model_fields) == ["age", "sum_insured"]
    args = tool.args_model.model_validate({"age": 30, "sum_insured": 100.0})
    assert args.age == 30


async def test_namespace_no_override_builtin(demo_session):
    """FR-002/Edge：与内置工具重名（mcp. 前缀内同名的既有注册）不被覆盖。"""
    from src.tools.mcp_client import McpSource

    class Shadow:
        name = "mcp.waiting_period"
        scope = "misc:calc"

    registry = Registry()
    sentinel = Shadow()  # 预占同名：注册必须跳过而非覆盖
    registry.register(sentinel)

    source = McpSource(registry, _settings())
    registered = await source.register_from_session(demo_session)
    assert "mcp.waiting_period" not in registered
    assert registry.get("mcp.waiting_period") is sentinel


async def test_mcp_call_via_executor(demo_session):
    """FR-015：MCP 工具经 ToolExecutor 同管道调用（口径与内置一致）。"""
    from src.tools.mcp_client import McpSource

    registry = Registry()
    source = McpSource(registry, _settings())
    await source.register_from_session(demo_session)

    executor = ToolExecutor(registry, _settings())
    ctx = ToolContext(tenant_id="t1", scopes=["misc:calc"])
    rec = await executor.execute(
        ToolCall(tool="mcp.premium_calc", query="保费", args={"age": 30, "sum_insured": 100.0}),
        ctx,
    )
    assert rec.ok is True
    assert rec.attempts == 1
    assert rec.result is not None
    assert "103" in rec.result.text  # 100 * (1 + 30/1000) = 103.0

    # scopes 不含 misc:calc → 执行前 permission 拒绝（同内置口径）
    rec2 = await executor.execute(
        ToolCall(tool="mcp.premium_calc", query="保费", args={"age": 30, "sum_insured": 100.0}),
        ToolContext(tenant_id="t1", scopes=[]),
    )
    assert rec2.ok is False and rec2.error_class == "permission"


async def test_register_tools_skips_invalid_schema():
    """Edge：清单含非法 schema（无 properties 的非对象类型）→ 跳过该工具其余正常。"""
    from mcp import types

    from src.tools.mcp_client import McpSource

    good = types.Tool(
        name="ok_tool",
        description="d",
        input_schema={"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]},
    )
    bad = types.Tool(name="bad_tool", description="d", input_schema={"type": "not-a-schema"})

    registry = Registry()
    source = McpSource(registry, _settings())
    registered = await source.register_tool_defs([good, bad])
    assert registered == ["mcp.ok_tool"]
    assert registry.get("mcp.bad_tool") is None


async def test_connect_unreachable_degrades_silently():
    """FR-014：Server 不可达 → connect 返回空、不抛出，注册表保持不变。"""
    from src.tools.mcp_client import McpSource

    registry = Registry()
    settings = Settings(
        mcp_server_cmd="definitely-not-exist-cmd-xyz", mcp_tool_scopes=["misc:calc"]
    )
    source = McpSource(registry, settings)
    registered = await source.connect()  # 告警日志不作为断言对象；验证降级不抛
    assert registered == []
    assert len(registry) == 0
