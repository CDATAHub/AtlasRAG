"""MCP 客户端（specs/003 US5；docs/04 §4.4）：外部 MCP Server 工具动态注册与统一调度。

- 契约自动生成：list_tools 的 inputSchema → 动态 Pydantic args_model（FR-013，
  不手工维护两份定义）
- 命名空间隔离：mcp.<name> 前缀，不覆盖既有注册（FR-002/Edge）
- 健康降级：来源不可达/清单非法 → warning + 跳过，不抛出、不影响内置工具（FR-014）
- 调度统一：注册后与内置工具共用 ToolExecutor 管道（FR-015）

连接生命周期：stdio 模式挂 FastAPI lifespan（connect/close，research D7）；
测试注入 in-memory session（章程 VII 零真实 IO）。
"""

import logging
from typing import Any, Literal

import pydantic
from mcp import ClientSession, types

from src.config import Settings
from src.tools.base import Registry, ToolContext, ToolPolicy

logger = logging.getLogger(__name__)

# JSON Schema 基本类型 → Python（research D7 最小映射；未收录类型跳过注册）
_SCHEMA_TYPES: dict[str, Any] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
}


def args_model_from_schema(tool_name: str, schema: dict) -> type[pydantic.BaseModel] | None:
    """inputSchema（object）→ 动态 args_model；非 object/未知字段类型返回 None（跳过注册）。"""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return None
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return None
    fields: dict[str, tuple[Any, Any]] = {}
    required = set(schema.get("required") or [])
    for name, spec in properties.items():
        py_type = _SCHEMA_TYPES.get(spec.get("type") if isinstance(spec, dict) else "")
        if py_type is None:
            return None
        # (类型, 默认)：Ellipsis = 必填（Pydantic 约定）；描述保留在工具 description
        fields[name] = (py_type, ...) if name in required else (py_type, None)
    return pydantic.create_model(  # noqa: PYD001 —— 动态契约的唯一合法入口
        f"{tool_name}_Args", **fields
    )


class McpTool:
    """MCP 来源工具：invoke = session.call_tool，结果文本化（无证据 hits）。"""

    def __init__(self, session: ClientSession, base_name: str, raw: types.Tool,
                 scopes: list[str]):
        self._session = session
        self._base_name = base_name
        self.name = f"mcp.{raw.name}"
        self.description = raw.description or f"MCP tool {raw.name}"
        self.scope = scopes[0] if scopes else "misc:calc"
        self.args_model = args_model_from_schema(raw.name, raw.input_schema)
        self.result_model = McpToolResult
        self.policy = ToolPolicy(timeout_ms=5000, max_retries=1, idempotent=True)

    async def invoke(self, ctx: ToolContext, args: pydantic.BaseModel) -> "McpToolResult":
        result = await self._session.call_tool(self._base_name, args.model_dump(exclude_none=True))
        if getattr(result, "isError", False):
            from src.tools.base import ToolError

            raise ToolError("; ".join(_content_text(c) for c in result.content))
        return McpToolResult(
            text="\n".join(_content_text(c) for c in result.content).strip()
        )


class McpToolResult(pydantic.BaseModel):
    """MCP 调用结果的统一载体：文本化（计算类演示工具足够；证据映射留后续场景）。"""

    text: str


def _content_text(content: Any) -> str:
    if isinstance(content, types.TextContent):
        return content.text
    return str(getattr(content, "data", ""))


class McpSource:
    """一个 MCP Server 连接：清单拉取 → 注册；拥有连接生命周期（stdio / 注入）。"""

    def __init__(self, registry: Registry, settings: Settings):
        self._registry = registry
        self._settings = settings
        self._session: ClientSession | None = None
        self._exit_stack = None

    async def register_from_session(self, session: ClientSession) -> list[str]:
        """拉取清单并注册（测试注入 session / 生产由 connect() 建立）。"""
        self._session = session
        listing = await session.list_tools()
        return await self.register_tool_defs(list(listing.tools))

    async def register_tool_defs(self, tool_defs: list[types.Tool]) -> list[str]:
        """注册清单（非法 schema / 重名跳过并告警，FR-014/Edge）。"""
        registered: list[str] = []
        for raw in tool_defs:
            name = f"mcp.{raw.name}"
            if self._registry.get(name) is not None:
                logger.warning("MCP 工具重名跳过（不覆盖既有注册）：%s", name)
                continue
            if not args_model_from_schema(raw.name, raw.input_schema):
                logger.warning("MCP 工具 inputSchema 非法，跳过注册：%s", name)
                continue
            self._registry.register(
                McpTool(self._session, raw.name, raw, self._settings.mcp_tool_scopes)
            )
            registered.append(name)
        return registered

    async def connect(self) -> list[str]:
        """stdio 连接演示 Server（settings.mcp_server_cmd），挂 FastAPI lifespan。"""
        import shlex

        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        if not self._settings.mcp_server_cmd:
            return []  # 未配置来源：静默不接入（开发态零依赖）
        from contextlib import AsyncExitStack

        self._exit_stack = AsyncExitStack()
        try:
            params = StdioServerParameters(
                command="uv", args=["run", *shlex.split(self._settings.mcp_server_cmd)]
            )
            read, write = await self._exit_stack.enter_async_context(stdio_client(params))
            session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await session.initialize()
            return await self.register_from_session(session)
        except Exception as exc:  # noqa: BLE001 —— FR-014：来源故障降级，不影响主服务
            await self.close()
            logger.warning("MCP 来源不可用，工具不接入：%s", exc)
            return []

    async def close(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None
