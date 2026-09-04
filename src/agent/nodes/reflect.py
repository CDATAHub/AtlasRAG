"""reflect 节点（docs/03 §3.4.5；research D9）：充分性评估，硬规则先行。

- 硬规则（确定性，零 LLM）：拒答、步数上限、回环轮数上限、直答路径、证据为空
- LLM 判据：证据覆盖/草稿自洽 → insufficient 时给 next_action 与 next_query，
  retrieve_more/rewrite_query/switch_tool 统一映射为 "replan"（图条件边回 plan，
  由 plan 节点保留已执行前缀、只追加未执行步骤——FR-005）
- continue_plan 映射为 converge：本图拓扑下 generate 仅在计划执行完后运行，
  「计划还有剩余步骤」不可能为真（注释留档，docs/03 §3.4.5 语义备查）
"""

import json
import logging
from typing_extensions import Literal

from pydantic import BaseModel, ValidationError

from src.config import Settings

logger = logging.getLogger(__name__)


class ReflectResult(BaseModel):
    sufficient: bool = False
    reason: str = ""
    next_action: Literal[
        "converge", "continue_plan", "retrieve_more", "rewrite_query", "switch_tool"
    ] = "converge"
    next_query: str | None = None


def make_reflect_node(llm, settings: Settings):
    async def reflect(state, config):  # noqa: ARG001
        # —— 硬规则先行（research D9）：任何一条命中即收敛，不消耗 LLM ——
        if state.get("refused"):
            return _converge(False, state.get("convergence_reason") or "refused")
        if state.get("direct_answer"):  # 直答路径（plan 判定常识）：不反思
            return _converge(True, "natural")
        if state.get("steps", 0) >= settings.max_steps:
            return _converge(False, "max_steps", convergence_reason="max_steps")
        rounds = state.get("plan_rounds", 1)
        if rounds >= settings.plan_rounds_max and rounds > 1:
            return _converge(True, "natural")  # 回环上限：有草稿则输出草稿（FR-006）
        if state.get("route") != "answer" and not (state.get("evidence") or []):
            return _converge(False, "refused", convergence_reason="refused")

        # —— LLM 充分性判据 ——
        messages = [
            {"role": "system", "content": _reflect_system()},
            {
                "role": "user",
                "content": (
                    f"用户问题：{state['question']}\n\n"
                    f"已执行检索：{json.dumps(_queries(state), ensure_ascii=False)}\n"
                    f"证据摘要：{json.dumps(_evidence_brief(state), ensure_ascii=False)}\n"
                    f"当前草稿：{state.get('draft') or ''}"
                ),
            },
        ]
        result = await llm.chat(messages, response_format={"type": "json_object"})
        rr = _parse_reflect(result.content)
        tokens = state.get("tokens_used", 0) + result.tokens
        if rr.sufficient or rr.next_action in ("converge", "continue_plan"):
            return _converge(True, "natural", tokens_used=tokens)
        return {
            "reflect_result": {
                "sufficient": rr.sufficient,
                "reason": rr.reason,
                "next_action": "replan",
                "next_query": rr.next_query,
            },
            "tokens_used": tokens,
        }

    return reflect


def _converge(sufficient: bool, reason: str, *, convergence_reason: str = "natural",
              tokens_used: int | None = None) -> dict:
    result: dict = {
        "reflect_result": {"sufficient": sufficient, "reason": reason, "next_action": "converge"}
    }
    if convergence_reason != "natural":
        result["convergence_reason"] = convergence_reason
    if tokens_used is not None:
        result["tokens_used"] = tokens_used
    return result


def _reflect_system() -> str:
    from src.agent.prompts import SYSTEM_REFLECTOR

    return SYSTEM_REFLECTOR


def _queries(state: dict) -> list[str]:
    return [t.get("query") or "" for t in state.get("tool_results") or []]


def _evidence_brief(state: dict) -> list[dict]:
    brief = []
    for entry in state.get("evidence") or []:
        for hit in entry.get("hits") or []:
            brief.append({"title": hit.get("title"), "sec_no": hit.get("sec_no"),
                          "score": hit.get("score")})
    return brief[:10]


def _parse_reflect(content: str) -> ReflectResult:
    try:
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("no json")
        return ReflectResult.model_validate(json.loads(content[start : end + 1]))
    except (ValueError, ValidationError, json.JSONDecodeError):
        # 解析失败按「充分」收敛：宁少回环，不冒死循环风险
        return ReflectResult(sufficient=True, reason="parse_failed", next_action="converge")
