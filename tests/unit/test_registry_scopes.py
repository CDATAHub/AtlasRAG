"""Registry 可见面 scopes 收敛单测（specs/003 US2；SC-005 的单测层）。

FR-003：required_scopes ⊆ 请求 scopes 才可见可调；部分覆盖视为无权；
空 scopes → 空工具面（US2 场景 3）。章程 VII：零真实外部服务。
"""

from src.config import Settings
from src.tools.base import Registry, ToolContext, ToolPolicy
from src.tools.executor import ToolCall, ToolExecutor

from tests.unit.fakes import FakeTool


class CalcScopedTool(FakeTool):
    """misc:calc 权限域的演示工具（模拟 MCP 演示工具的 scope 形态）。"""

    name = "mcp.calc"
    scope = "misc:calc"
    policy = ToolPolicy(timeout_ms=200, max_retries=1, required_scopes=("misc:calc",))


class NeedsBothTool(FakeTool):
    """二元权限域工具：验证部分覆盖视为无权（FR-003/Edge）。"""

    name = "needs_both"
    scope = "retrieval:read"
    policy = ToolPolicy(
        timeout_ms=200, max_retries=0, required_scopes=("retrieval:read", "misc:calc")
    )


class BareTool(FakeTool):
    """未声明 policy 的工具（resolve_policy 走单值 scope 缺省分支）。"""

    name = "bare"
    policy = None  # type: ignore[assignment]  # 模拟未声明


def _registry() -> tuple[Registry, FakeTool, CalcScopedTool]:
    retrieval = FakeTool()
    calc = CalcScopedTool()
    registry = Registry()
    registry.register(retrieval)
    registry.register(calc)
    return registry, retrieval, calc


def test_retrieval_scope_sees_only_retrieval_tools():
    registry, retrieval, calc = _registry()
    visible = [t["name"] for t in registry.visible_tools(["retrieval:read"])]
    assert visible == [retrieval.name]  # misc:calc 不可见
    assert "mcp.calc" not in visible


def test_calc_scope_sees_only_calc_tools():
    registry, *_ = _registry()
    visible = [t["name"] for t in registry.visible_tools(["misc:calc"])]
    assert visible == ["mcp.calc"]  # 检索工具不可见（US2 场景 1 反向）


def test_empty_scopes_yields_empty_face():
    """空 scopes → 空工具面（US2 场景 3：走既有降级路径，不崩溃）。"""
    registry, *_ = _registry()
    assert registry.visible_tools([]) == []


def test_multi_scope_union_visible():
    registry, *_ = _registry()
    visible = {t["name"] for t in registry.visible_tools(["retrieval:read", "misc:calc"])}
    assert visible == {"fake", "mcp.calc"}


def test_multi_required_scopes_need_full_coverage():
    """部分覆盖（所需为二元组、请求只含其一）视为无权（FR-003/Edge）。"""
    registry = Registry()
    registry.register(NeedsBothTool())
    assert registry.visible_tools(["retrieval:read"]) == []
    assert [t["name"] for t in registry.visible_tools(["retrieval:read", "misc:calc"])] == [
        "needs_both"
    ]


async def test_executor_denies_unauthorized_without_invoke():
    """US2 场景 2：越权调用执行前被拒（error_class=permission），实现未执行。"""
    registry, retrieval, _ = _registry()
    executor = ToolExecutor(registry, Settings(tool_default_timeout_ms=200))
    ctx = ToolContext(session=None, tenant_id="t1", scopes=["misc:calc"])  # 无检索权限
    rec = await executor.execute(
        ToolCall(tool="fake", query="q", args={"query": "q"}), ctx
    )
    assert rec.ok is False
    assert rec.error_class == "permission"
    assert retrieval.invoke_count == 0


def test_default_policy_falls_back_to_scope():
    """无 policy 声明的工具：required_scopes 取单值 scope（research D1 sugar）。"""
    registry = Registry()
    registry.register(BareTool())
    assert [t["name"] for t in registry.visible_tools(["retrieval:read"])] == ["bare"]
    assert [t["name"] for t in registry.visible_tools(["other:scope"])] == []


async def test_plan_node_empty_scopes_degrades_to_direct_answer():
    """US2 场景 3（plan 层）：注册表有工具但请求 scopes 不覆盖 → 直答降级，不调 LLM。"""
    from unittest.mock import patch

    from src.agent.nodes.plan import make_plan_node
    from tests.unit.fakes import FakeLLM

    registry, *_ = _registry()  # 注册了 fake(retrieval:read) 与 mcp.calc(misc:calc)
    llm = FakeLLM()
    node = make_plan_node(llm, Settings(), registry)
    with patch("src.agent.nodes.plan.get_stream_writer", return_value=lambda e: None):
        state = {"question": "等待期多久？", "scopes": [], "messages": []}
        out = await node(state, {})
    assert out["route"] == "answer"  # 既有降级路径（generate 直答，不编造条款）
    assert llm.chat_calls == []  # 未消耗规划 LLM


def test_parse_plan_calls_normalization():
    """US4/T014：calls 规整——截断 ≤4、空 tool 回退步 tool、空 query 回退步 query。"""
    from src.agent.nodes.plan import _parse_plan

    raw = {
        "route": "retrieve",
        "plan": [
            {
                "step": 1,
                "tool": "hybrid_search",
                "query": "q0",
                "calls": [
                    {"tool": "", "query": "a"},
                    {"query": "b"},
                    {"tool": "doc_reader", "query": ""},
                ]
                + [{"tool": "hybrid_search", "query": f"x{i}"} for i in range(4)],
            }
        ],
    }
    import json

    result = _parse_plan(json.dumps(raw, ensure_ascii=False), "问题")
    calls = result.plan[0].calls
    assert len(calls) == 4  # 截断
    assert calls[0]["tool"] == "hybrid_search"  # 空 tool 回退
    assert calls[1]["tool"] == "hybrid_search"
    assert calls[2]["query"] == "q0"  # 空 query 回退步 query
