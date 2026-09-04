"""工具契约层（章程 I/III，research D5）：模型可见面的唯一入口。

- `Tool` Protocol：name/description/scope + Pydantic 入出参契约 + invoke
- `Registry`：注册表与 `visible_tools(scopes)` —— 模型只能看到其有权限的工具
- `ToolContext`：请求级注入（DB 会话 + 租户），工具实现不持有请求状态
- 阶段 3 执行引擎生产化（并行/重试/幂等中间件）在此层演进，图与契约不变
"""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ToolContext:
    """一次图执行内的工具调用上下文（research D5）。"""

    session: AsyncSession
    tenant_id: str


class ToolError(Exception):
    """工具执行失败（网络/上游故障等）。不中断循环：由 reflect 决策（FR-004）。"""


class Tool(Protocol):
    name: str
    description: str
    scope: str
    args_model: type[BaseModel]
    result_model: type[BaseModel]

    async def invoke(self, ctx: ToolContext, args: BaseModel) -> BaseModel: ...


class Registry:
    """名称 → 工具；visible_tools 即模型可见工具面（章程 I 收敛）。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def visible_tools(self, scopes: list[str]) -> list[dict]:
        """scope 收敛后的可见面（name/description/参数摘要）；plan prompt 由此生成。"""
        return [
            {
                "name": t.name,
                "description": t.description,
                "args": list(t.args_model.model_fields.keys()),
            }
            for t in self._tools.values()
            if t.scope in scopes
        ]
