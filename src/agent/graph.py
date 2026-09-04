"""AgentLoop 图构建（docs/03 §3.8 骨架；research D1/D2）。

六节点：plan → route → tool_node → generate → reflect → converge；
tool_node 执行完回 route 判断剩余计划；reflect 条件边回 plan（US2 回环）或 converge。
per-run 依赖（DB 会话、ToolContext）经 config["configurable"] 注入；app 级依赖
（LLM、Registry、Settings）在构建期闭包注入——测试可整体替换（章程 VII）。
"""

import functools

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.agent.state import AgentState
from src.config import Settings
from src.tools.base import Registry

# config["configurable"] 中 per-run 注入的键
DB_SESSION_KEY = "db"  # 当前请求的 AsyncSession（整个图执行共用一个事务域外会话）


def build_graph(
    *,
    llm,
    registry: Registry,
    settings: Settings,
    checkpointer: BaseCheckpointSaver,
):
    """构建并编译 AgentLoop 图。app 级依赖闭包注入，测试可整体替换。"""

    # —— 节点实现按任务清单分阶段补齐（US1: T012–T016）；此处先接线占位 ——
    from src.agent.nodes.converge import make_converge_node
    from src.agent.nodes.generate import make_generate_node
    from src.agent.nodes.plan import make_plan_node
    from src.agent.nodes.reflect import make_reflect_node
    from src.agent.nodes.route import make_route_node
    from src.agent.nodes.tool_node import make_tool_node

    plan = make_plan_node(llm, settings, registry)
    tool_node = make_tool_node(registry)
    generate = make_generate_node(llm, settings)
    reflect = make_reflect_node(llm, settings)

    g = StateGraph(AgentState)
    g.add_node("plan", plan)
    g.add_node("route", make_route_node(settings))
    g.add_node("tool_node", tool_node)
    g.add_node("generate", generate)
    g.add_node("reflect", reflect)
    g.add_node("converge", make_converge_node())

    g.add_edge(START, "plan")
    g.add_edge("plan", "route")
    g.add_conditional_edges(
        "route",
        _route_fn,
        {"tool": "tool_node", "generate": "generate"},
    )
    g.add_edge("tool_node", "route")  # 执行完回路由，判断剩余计划（docs/03 循环粒度）
    g.add_edge("generate", "reflect")
    g.add_conditional_edges("reflect", _reflect_fn, {"converge": "converge", "plan": "plan"})
    g.add_edge("converge", END)

    return g.compile(checkpointer=checkpointer)


def _route_fn(state: AgentState) -> str:
    return "generate" if state.get("route") == "answer" else "tool"


def _reflect_fn(state: AgentState) -> str:
    """US1：reflect 仅硬规则，恒收敛；US2 扩展回 plan 的回环分支。"""
    if state.get("reflect_result", {}).get("next_action") == "replan":
        return "plan"
    return "converge"


def build_tool_registry(embedding, reranker, settings: Settings) -> Registry:
    """应用级工具注册表：本阶段仅注册已实现的 hybrid_search（clarify Q1）。"""
    from src.tools.hybrid_search import HybridSearchTool

    registry = Registry()
    registry.register(
        HybridSearchTool(
            embedding,
            reranker,
            hybrid_top_k=settings.hybrid_top_k,
            rerank_top_k=settings.rerank_top_k,
            use_rerank=settings.use_rerank,
        )
    )
    return registry
