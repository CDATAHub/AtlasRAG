"""工具契约层（章程 I/III，research D5→specs/003 D1）：模型可见面的唯一入口。

- `Tool` Protocol：name/description/scope + Pydantic 入出参契约 + invoke
  + 可选 `policy`（specs/003 FR-001 执行策略元数据；缺省按 Settings 保守默认解析）
- `Registry`：注册表与 `visible_tools(scopes)` —— required_scopes ⊆ 请求 scopes
  的覆盖判定，模型只能看到其有权限的工具（FR-003）
- `ToolContext`：请求级注入（DB 会话 + 租户 + scopes），工具实现不持有请求状态
- 执行引擎（specs/003 US1）在 `executor.py` 演进，图与契约不变
"""

from dataclasses import dataclass, field
from typing import ClassVar, Protocol

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ToolContext:
    """一次图执行内的工具调用上下文（research D5）。

    session 可空：无 DB 依赖的工具（如 MCP 计算类，specs/003 US5）不注入会话。
    """

    session: AsyncSession | None = None
    tenant_id: str = ""
    scopes: list[str] = field(default_factory=list)  # specs/003 US2：请求权限域


class ToolError(Exception):
    """工具执行失败（网络/上游故障等）。不中断循环：由 reflect 决策（FR-004）。"""


class ToolTimeoutError(ToolError):
    """执行超过契约 timeout_ms（specs/003 FR-006；可重试）。"""


class ToolTransientError(ToolError):
    """瞬时故障（网络抖动/5xx，specs/003 FR-007；可重试）。"""


@dataclass(frozen=True)
class ToolPolicy:
    """工具执行策略元数据（specs/003 FR-001 / data-model.md 表）。

    未声明时按 Settings 保守默认解析（required_scopes 取工具单值 scope）。
    """

    timeout_ms: int = 5000
    max_retries: int = 1
    idempotent: bool = True
    parallel_safe: bool = True
    required_scopes: tuple[str, ...] = ()


class Tool(Protocol):
    name: ClassVar[str]
    description: ClassVar[str]
    scope: ClassVar[str]
    args_model: ClassVar[type[BaseModel]]
    result_model: ClassVar[type[BaseModel]]
    policy: ToolPolicy  # specs/003：可选声明（实例或类级），缺省由 Executor 解析默认

    async def invoke(self, ctx: ToolContext, args: BaseModel) -> BaseModel: ...


def resolve_policy(tool: Tool, *, default_timeout_ms: int, default_max_retries: int) -> ToolPolicy:
    """解析工具生效策略：显式声明优先，缺省保守回填（FR-001）。"""
    declared = getattr(tool, "policy", None)
    if declared is None:
        return ToolPolicy(
            timeout_ms=default_timeout_ms,
            max_retries=default_max_retries,
            required_scopes=(tool.scope,),  # 单值 scope 作 sugar
        )
    return ToolPolicy(
        timeout_ms=declared.timeout_ms or default_timeout_ms,
        max_retries=declared.max_retries,
        idempotent=declared.idempotent,
        parallel_safe=declared.parallel_safe,
        required_scopes=declared.required_scopes or (tool.scope,),
    )


class Registry:
    """名称 → 工具；visible_tools 即模型可见工具面（章程 I 收敛）。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def __len__(self) -> int:
        return len(self._tools)

    def visible_tools(self, scopes: list[str]) -> list[dict]:
        """scope 收敛后的可见面（name/description/参数摘要）；plan prompt 由此生成。

        required_scopes ⊆ 请求 scopes 才可见（FR-003 覆盖判定，部分覆盖视为无权）。
        """
        granted = set(scopes)
        return [
            {
                "name": t.name,
                "description": t.description,
                "args": list(t.args_model.model_fields.keys()),
            }
            for t in self._tools.values()
            if set(_required_scopes(t)) <= granted
        ]


def _required_scopes(tool: Tool) -> tuple[str, ...]:
    declared = getattr(tool, "policy", None)
    if declared and declared.required_scopes:
        return declared.required_scopes
    return (tool.scope,)
