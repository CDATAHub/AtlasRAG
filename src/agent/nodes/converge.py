"""converge 节点（docs/03 §3.4.6）：组装最终引用；done 事件由编排层基于
图终态统一构造（含 latency/收敛原因），保证任何终止路径事件面一致。"""

from langgraph.config import get_stream_writer


def make_converge_node():
    async def converge(state, config):  # noqa: ARG001
        writer = get_stream_writer()
        writer({"type": "citations", "citations": state.get("citations") or []})
        return {"final_answer": state.get("draft") or ""}

    return converge
