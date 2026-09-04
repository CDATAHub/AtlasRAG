"""plan 节点（docs/03 §3.4.1；research D4）：结构化规划 + route 判定 + query 改写。

输出 JSON 经 Pydantic 校验；解析失败重试 1 次，仍失败降级「单步检索」保底
（spec Edge）。plan 事件经 stream writer 外发（research D3）。
"""

import json
import logging

from pydantic import BaseModel, ValidationError
from typing_extensions import Literal

from langgraph.config import get_stream_writer

from src.agent.prompts import SYSTEM_PLANNER
from src.tools.base import Registry

logger = logging.getLogger(__name__)


class PlanStep(BaseModel):
    step: int = 1
    action: Literal["retrieve", "tool"] = "retrieve"
    tool: str = "hybrid_search"
    query: str
    rationale: str = ""


class PlanResult(BaseModel):
    route: Literal["retrieve", "answer"] = "retrieve"
    plan: list[PlanStep] = []


def make_plan_node(llm, registry: Registry):
    tools_desc = json.dumps(registry.visible_tools(["retrieval:read"]), ensure_ascii=False)

    async def plan(state, config):  # noqa: ARG001 —— config 为 LangGraph 节点签名
        writer = get_stream_writer()
        rounds = state.get("plan_rounds", 0) + 1
        question = state["question"]
        history = _history_text(state.get("messages") or [], question)

        # 重规划（FR-005）：保留已执行前缀，只替换未执行部分
        prior_plan = state.get("plan") or []
        executed = prior_plan[: state.get("current_step", 0)] if rounds > 1 else []
        reflect = state.get("reflect_result") or {}

        system = SYSTEM_PLANNER.replace("{tools}", tools_desc)
        user = f"近期对话：\n{history}\n\n当前问题：{question}" if history else f"当前问题：{question}"
        if rounds > 1:
            user += (
                f"\n\n这是第 {rounds} 轮规划。此前计划已执行但证据不足"
                f"（反思结论：{reflect.get('reason') or '未覆盖'}；"
                f"建议动作：{reflect.get('next_action') or 'rewrite_query'}）。\n"
                f"已执行检索式：{[s.get('query') for s in executed]}\n"
                "要求：保留已完成检索成果，只输出 1~2 个「后续」检索步骤"
                "（改写检索式/换关键词/补充检索，不要重复已执行检索式）。"
            )
        fallback_query = reflect.get("next_query") or question

        result, tokens = await _plan_with_retry(llm, system, user, fallback_query, question)
        new_steps = [s.model_dump() for s in result.plan]
        for offset, s in enumerate(new_steps, start=len(executed) + 1):
            s["step"] = offset
        steps = executed + new_steps
        current = len(executed)

        writer(
            {
                "type": "plan",
                "round": rounds,
                "session_id": state.get("session_id"),
                "message_id": state.get("message_id"),
                "steps": [
                    {k: s[k] for k in ("step", "action", "tool", "query", "rationale")}
                    for s in new_steps
                ],
            }
        )
        route = result.route if (rounds == 1 or not executed) else "retrieve"
        return {
            "plan": steps,
            "route": route,
            "current_step": current,
            "plan_rounds": rounds,
            "tokens_used": state.get("tokens_used", 0) + tokens,
        }

    return plan


async def _plan_with_retry(
    llm, system: str, user: str, fallback_query: str, question: str
) -> tuple[PlanResult, int]:
    """返回 (PlanResult, 本轮 usage tokens)；解析失败重试 1 次（spec Edge）。"""
    last_err: Exception | None = None
    total = 0
    for attempt in range(2):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if attempt == 1:
            messages.append(
                {
                    "role": "user",
                    "content": "上一次输出无法解析。严格按约定 JSON 结构重新输出，不要任何多余文本。",
                }
            )
        result = await llm.chat(messages, response_format={"type": "json_object"})
        total += result.tokens
        try:
            return _parse_plan(result.content, question), total
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_err = exc
    logger.warning("plan 解析失败，降级单步检索：%s", last_err)
    fallback = PlanResult(route="retrieve", plan=[PlanStep(step=1, query=fallback_query)])
    return fallback, total


def _parse_plan(content: str, question: str) -> PlanResult:
    data = json.loads(_json_slice(content))
    result = PlanResult.model_validate(data)
    if result.route == "retrieve" and not result.plan:
        raise ValueError("empty plan")
    if result.route == "retrieve":
        for i, s in enumerate(result.plan, start=1):
            if not s.query:
                s.query = question  # 空检索式回退原问题
            s.step = i
    return result


def _json_slice(content: str) -> str:
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end == -1:
        raise json.JSONDecodeError("no json", content, 0)
    return content[start : end + 1]


def _history_text(messages: list, question: str) -> str:
    """除当前问题外的近期对话（US4 压缩后的结果），含当前问题在内的完整历史由路由层组装。"""
    lines: list[str] = []
    for m in messages:
        content = getattr(m, "content", None)
        if content is None and isinstance(m, dict):
            content = m.get("content", "")
        if str(content).strip() and str(content) != question:
            lines.append(str(content))
    return "\n".join(lines[-8:])
