"""tool_node（docs/03 §3.4.3；research D5→specs/003 US1）：按计划步经执行引擎调用工具。

结果经 ToolExecutor 统一管道（权限/校验/超时/重试/恢复，FR-004~010）；
record 序列化写入 tool_results 与 evidence（进入生成上下文）；
失败记入 evidence 标记 failed、不中断循环；tool_call/evidence 事件外发
（tool_call 携带可选 attempts/duration_ms，contracts v2.1）。
"""

from langgraph.config import get_stream_writer

from src.tools.base import ToolContext
from src.tools.executor import ToolCall, ToolExecutor


def make_tool_node(executor: ToolExecutor):
    async def tool_node(state, config):  # noqa: ARG001
        writer = get_stream_writer()
        plan = state.get("plan") or []
        idx = state.get("current_step", 0)
        step = plan[idx]

        ctx = ToolContext(
            session=config["configurable"]["db"],
            tenant_id=state["tenant_id"],
            # scopes 由 chat 层注入（US2/T009）；直跑图未注入回退缺省，
            # 显式空列表 = 空权限域（executor 将以 permission 拒绝）
            scopes=state.get("scopes") if state.get("scopes") is not None else ["retrieval:read"],
        )
        step_no = step.get("step", idx + 1)
        trace_id = state.get("trace_id")
        round_no = state.get("plan_rounds", 1)

        calls_spec = step.get("calls") or []
        if len(calls_spec) > 1:  # specs/003 US4（clarify Q1）：一步多调用并行/串行编排
            calls = [
                ToolCall(
                    tool=(c.get("tool") or step.get("tool") or "hybrid_search"),
                    query=(c.get("query") or step.get("query") or ""),
                    args={"query": c.get("query") or step.get("query") or ""},
                )
                for c in calls_spec
            ]
        else:
            query = step.get("query") or ""
            calls = [ToolCall(tool=step.get("tool") or "", query=query, args={"query": query})]

        records = await executor.execute_step(calls, ctx, step=step_no, trace_id=trace_id)

        entries: list[dict] = []
        for call, record in zip(calls, records, strict=True):  # 每调用一条事件（contracts v2.1）
            writer(
                {
                    "type": "tool_call",
                    "step": step_no,
                    "tool": call.tool,
                    "query": call.query,
                    "attempts": record.attempts,
                    "duration_ms": record.duration_ms,
                }
            )
            entry = record.to_entry()
            writer(
                {
                    "type": "evidence",
                    "round": round_no,
                    "trace_id": trace_id,
                    "hits": [
                        {k: h[k] for k in ("n", "doc_id", "title", "sec_no", "score")}
                        for h in entry.get("hits") or []
                    ],
                    **({} if entry.get("ok") else {"failed": True}),
                }
            )
            entries.append(entry)

        return {
            "tool_results": entries,
            "evidence": entries,
            "current_step": idx + 1,
            "steps": state.get("steps", 0) + 1,
        }

    return tool_node
