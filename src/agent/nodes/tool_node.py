"""tool_node（docs/03 §3.4.3；research D5）：按计划步经 Registry 执行工具。

结果写入 tool_results（原始返回）与 evidence（进入生成上下文）；
失败记入 evidence 标记 failed、不中断循环（FR-004）；tool_call/evidence 事件外发。
"""

from langgraph.config import get_stream_writer
from pydantic import ValidationError

from src.tools.base import Registry, ToolContext, ToolError


def make_tool_node(registry: Registry):
    async def tool_node(state, config):  # noqa: ARG001
        writer = get_stream_writer()
        plan = state.get("plan") or []
        idx = state.get("current_step", 0)
        step = plan[idx]
        query = step.get("query") or ""
        tool = registry.get(step.get("tool") or "")

        writer(
            {
                "type": "tool_call",
                "step": step.get("step", idx + 1),
                "tool": step.get("tool") or "",
                "query": query,
            }
        )

        entry = {"tool": step.get("tool"), "query": query, "step": step.get("step", idx + 1)}
        if tool is None:
            entry.update(ok=False, error=f"unknown tool: {step.get('tool')}", hits=[])
        else:
            try:
                args = tool.args_model.model_validate({"query": query})
                ctx = ToolContext(
                    session=config["configurable"]["db"], tenant_id=state["tenant_id"]
                )
                result = await tool.invoke(ctx, args)
                entry.update(
                    ok=True,
                    hits=[hit.model_dump() for hit in result.hits],
                    top_score=result.top_score,
                )
            except (ToolError, ValidationError) as exc:
                entry.update(ok=False, error=str(exc), hits=[])

        round_no = state.get("plan_rounds", 1)
        writer(
            {
                "type": "evidence",
                "round": round_no,
                "trace_id": state.get("trace_id"),
                "hits": [
                    {k: h[k] for k in ("n", "doc_id", "title", "sec_no", "score")}
                    for h in entry.get("hits") or []
                ],
                **({} if entry.get("ok") else {"failed": True}),
            }
        )

        return {
            "tool_results": [entry],
            "evidence": [entry],
            "current_step": idx + 1,
            "steps": state.get("steps", 0) + 1,
        }

    return tool_node
