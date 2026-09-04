"""reflect 节点（docs/03 §3.4.5）：充分性评估，硬规则先行（research D9）。

US1 最小版：拒答/步数上限/证据为空等硬规则直接收敛；LLM 充分性判据与
回环重规划（next_action=retrieve_more/rewrite_query/switch_tool）属 US2（T022）。
"""

from langgraph.config import get_stream_writer

from src.config import Settings


def make_reflect_node(settings: Settings):
    async def reflect(state, config):  # noqa: ARG001
        writer = get_stream_writer()

        if state.get("refused"):
            return {
                "reflect_result": {
                    "sufficient": False,
                    "reason": state.get("convergence_reason") or "refused",
                    "next_action": "converge",
                }
            }
        if state.get("steps", 0) >= settings.max_steps:
            return {
                "reflect_result": {
                    "sufficient": False,
                    "reason": "max_steps",
                    "next_action": "converge",
                },
                "convergence_reason": "max_steps",
            }
        if not (state.get("evidence") or []):
            return {
                "reflect_result": {
                    "sufficient": False,
                    "reason": "no_evidence",
                    "next_action": "converge",
                },
                "convergence_reason": "refused",
            }

        return {
            "reflect_result": {"sufficient": True, "reason": "covered", "next_action": "converge"}
        }

    return reflect
