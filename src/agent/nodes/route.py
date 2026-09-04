"""route 节点（docs/03 §3.4.2）：纯确定性路由，无 LLM。

规则：plan 判定直答 → generate；计划未执行完 → tool_node；
计划执行完或达步数上限 → generate（research D9 硬规则先行）。
"""

from src.config import Settings


def make_route_node(settings: Settings):
    async def route(state, config):  # noqa: ARG001 —— LangGraph 节点签名
        if state.get("route") == "answer":
            return {"route": "answer"}
        plan = state.get("plan") or []
        exhausted = state.get("current_step", 0) >= len(plan)
        capped = state.get("steps", 0) >= settings.max_steps
        return {"route": "answer" if (exhausted or capped) else "tool"}

    return route
