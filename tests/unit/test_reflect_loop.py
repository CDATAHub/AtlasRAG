"""T020：reflect 充分性判据 + 重规划「只替换未执行步骤」不变式（US2）。"""

import json
from unittest.mock import patch

import pytest

from src.agent.nodes.plan import make_plan_node
from src.agent.nodes.reflect import ReflectResult, _parse_reflect, make_reflect_node
from src.config import get_settings
from src.tools.base import Registry
from tests.unit.fakes import FakeLLM

SETTINGS = get_settings()

REFLECT_INSUFFICIENT = json.dumps(
    {
        "sufficient": False,
        "reason": "未覆盖等待期条款",
        "next_action": "rewrite_query",
        "next_query": "重疾险 等待期",
    },
    ensure_ascii=False,
)
REFLECT_SUFFICIENT = json.dumps({"sufficient": True, "reason": "已覆盖", "next_action": "converge"})


def test_parse_reflect_valid():
    rr = _parse_reflect(REFLECT_INSUFFICIENT)
    assert rr.sufficient is False
    assert rr.next_action == "rewrite_query"
    assert rr.next_query == "重疾险 等待期"


def test_parse_reflect_invalid_defaults_converge():
    rr = _parse_reflect("完全不是 JSON")
    assert rr.next_action == "converge"  # 解析失败按充分处理，避免死循环


async def test_reflect_hard_rules_first():
    """硬规则优先：拒答/步数上限/直答路径不调 LLM。"""
    node = make_reflect_node(FakeLLM(), SETTINGS)

    result = await node({"refused": True, "convergence_reason": "refused"}, {})
    assert result["reflect_result"]["next_action"] == "converge"

    result = await node({"steps": SETTINGS.max_steps}, {})
    assert result["convergence_reason"] == "max_steps"

    result = await node({"route": "answer", "direct_answer": True}, {})
    assert result["reflect_result"]["sufficient"] is True  # 直答路径不反思


async def test_reflect_llm_insufficient_maps_to_replan():
    llm = FakeLLM(chat_responses=[REFLECT_INSUFFICIENT])
    node = make_reflect_node(llm, SETTINGS)
    state = {"question": "q", "route": "retrieve", "evidence": [{"hits": [{}]}],
             "draft": "草稿", "plan_rounds": 1}
    result = await node(state, {})
    assert result["reflect_result"]["next_action"] == "replan"  # 图条件边据此回 plan
    assert result["reflect_result"]["next_query"] == "重疾险 等待期"
    assert result["tokens_used"] == 100  # usage 记账


async def test_reflect_rounds_cap_forces_converge():
    """FR-006：回环达上限（3 轮）强制收敛，不再调 LLM。"""
    llm = FakeLLM(chat_responses=[REFLECT_INSUFFICIENT] * 5)
    node = make_reflect_node(llm, SETTINGS)
    state = {"route": "retrieve", "evidence": [{"hits": [{}]}], "draft": "草稿", "plan_rounds": 3}
    result = await node(state, {})
    assert result["reflect_result"]["next_action"] == "converge"
    assert llm.chat_calls == []  # 硬规则先行，未消耗 LLM


async def test_plan_replan_keeps_executed_prefix():
    """FR-005 不变式：重规划保留已执行步骤，只追加未执行部分。"""
    llm = FakeLLM(
        chat_responses=[
            json.dumps(
                {"route": "retrieve", "plan": [{"step": 1, "query": "改写检索式"}]},
                ensure_ascii=False,
            )
        ]
    )
    node = make_plan_node(llm, SETTINGS, Registry())
    events: list[dict] = []
    with patch("src.agent.nodes.plan.get_stream_writer", return_value=events.append):
        state = await node(
            {
                "question": "q",
                "messages": [{"role": "user", "content": "q"}],
                "plan": [{"step": 1, "action": "retrieve", "tool": "hybrid_search",
                          "query": "原检索式", "rationale": ""}],
                "current_step": 1,  # 已执行完首轮计划
                "plan_rounds": 1,
                "reflect_result": {"sufficient": False, "reason": "不足",
                                   "next_action": "replan", "next_query": "改写检索式"},
            },
            {},
        )
    assert state["plan_rounds"] == 2
    assert state["current_step"] == 1  # 已执行前缀保留，不重跑
    assert [s["query"] for s in state["plan"]] == ["原检索式", "改写检索式"]  # 前缀 + 新步骤
    assert state["plan"][1]["step"] == 2  # 编号续接
    assert state["route"] == "retrieve"
