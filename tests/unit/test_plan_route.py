"""T011：plan 结构化输出与 route 确定性路由（US1，纯单元无 IO）。"""

import json

import pytest

from src.agent.nodes.plan import PlanResult, _parse_plan, _plan_with_retry, make_plan_node
from src.agent.nodes.route import make_route_node
from src.config import get_settings
from src.tools.base import Registry
from tests.unit.fakes import FakeLLM

SETTINGS = get_settings()

TWO_STEP_PLAN = {
    "route": "retrieve",
    "plan": [
        {"step": 1, "action": "retrieve", "tool": "hybrid_search",
         "query": "重疾险 等待期", "rationale": "查等待期定义"},
        {"step": 2, "action": "retrieve", "tool": "hybrid_search",
         "query": "等待期 出险 保险责任", "rationale": "查等待期内出险责任"},
    ],
}


def test_parse_plan_valid():
    result = _parse_plan(json.dumps(TWO_STEP_PLAN, ensure_ascii=False), "q")
    assert result.route == "retrieve"
    assert [s.query for s in result.plan] == ["重疾险 等待期", "等待期 出险 保险责任"]


def test_parse_plan_wrapped_in_prose():
    content = '前置说明 {"route":"answer","plan":[]} 后置说明'
    assert _parse_plan(content, "q").route == "answer"


def test_parse_plan_retrieve_with_empty_plan_is_invalid():
    with pytest.raises(ValueError):
        _parse_plan('{"route": "retrieve", "plan": []}', "q")


def test_parse_plan_empty_query_backfills_question():
    content = json.dumps(
        {"route": "retrieve", "plan": [{"step": 1, "query": ""}]}, ensure_ascii=False
    )
    result = _parse_plan(content, "原问题")
    assert result.plan[0].query == "原问题"
    assert result.plan[0].step == 1


async def test_plan_retry_then_fallback_single_step():
    """spec Edge：两次解析失败 → 降级单步检索，检索式回退原问题。"""
    llm = FakeLLM(chat_responses=["不是 JSON", "{}"])
    result, tokens = await _plan_with_retry(llm, "sys", "user", "等待期多久？", "等待期多久？")
    assert result.route == "retrieve"
    assert len(result.plan) == 1
    assert result.plan[0].query == "等待期多久？"
    assert len(llm.chat_calls) == 2  # 重试 1 次
    assert llm.response_formats == [{"type": "json_object"}] * 2
    assert tokens > 0  # usage 记账（FR-007 预算口径）


async def test_plan_node_emits_event_and_state():
    llm = FakeLLM(chat_responses=[json.dumps(TWO_STEP_PLAN, ensure_ascii=False)])
    registry = Registry()
    node = make_plan_node(llm, registry)
    events: list[dict] = []

    from unittest.mock import patch

    with patch("src.agent.nodes.plan.get_stream_writer", return_value=events.append):
        state = await node(
            {"question": "q", "messages": [{"role": "user", "content": "q"}], "session_id": "s"},
            {},
        )
    assert state["plan_rounds"] == 1 and state["current_step"] == 0
    assert len(state["plan"]) == 2
    assert events[0]["type"] == "plan" and len(events[0]["steps"]) == 2


async def test_route_three_branches():
    route = make_route_node(SETTINGS)

    assert (await route({"route": "answer"}, {}))["route"] == "answer"  # 直答
    assert (await route({"route": "retrieve", "plan": [{"step": 1}], "current_step": 0}, {}))[
        "route"
    ] == "tool"  # 计划未执行完
    assert (await route({"route": "retrieve", "plan": [{"step": 1}], "current_step": 1}, {}))[
        "route"
    ] == "answer"  # 计划执行完
    assert (await route({"route": "retrieve", "plan": [{"step": 1}] * 6, "current_step": 0,
                         "steps": SETTINGS.max_steps}, {}))["route"] == "answer"  # 步数上限
