"""T026：三重收敛保险的纯判定（US3）——预算边界、降级输出、超时熔断。"""

from unittest.mock import patch

import pytest

from src.agent.guards import BUDGET_DEGRADED_TEXT, budget_exhausted, budget_stop
from src.agent.nodes.generate import make_generate_node
from src.agent.nodes.plan import make_plan_node
from src.config import get_settings
from src.tools.base import Registry
from tests.unit.fakes import FakeLLM

SETTINGS = get_settings()


def test_budget_boundary_equal_counts_as_exhausted():
    assert budget_exhausted({"tokens_used": SETTINGS.token_budget}, SETTINGS) is True
    assert budget_exhausted({"tokens_used": SETTINGS.token_budget - 1}, SETTINGS) is False
    assert budget_exhausted({}, SETTINGS) is False  # 首轮（0）不触发


def test_budget_stop_payload_shape():
    stop = budget_stop({"evidence": [{"hits": [{"n": 1}]}]})
    assert stop["refused"] is True
    assert stop["convergence_reason"] == "budget"
    assert stop["citations"] == []  # 不编造
    assert stop["hit_count"] == 1


async def test_plan_skips_llm_when_budget_exhausted():
    """预算耗尽（重规划轮）→ 不再调 LLM 规划，直接转生成收敛。"""
    llm = FakeLLM()
    node = make_plan_node(llm, SETTINGS, Registry())
    state = {"question": "q", "messages": [{"role": "user", "content": "q"}],
             "tokens_used": SETTINGS.token_budget, "plan_rounds": 1}
    with patch("src.agent.nodes.plan.get_stream_writer", return_value=lambda _: None):
        result = await node(state, {})
    assert result["route"] == "answer"
    assert llm.chat_calls == []  # 拒发 LLM 调用


async def test_generate_budget_degradation():
    """预算耗尽 → 降级提示输出，convergence_reason=budget。"""
    events: list[dict] = []
    node = make_generate_node(FakeLLM(), SETTINGS)
    with patch("src.agent.nodes.generate.get_stream_writer", return_value=events.append):
        result = await node(
            {"question": "q", "route": "retrieve", "tokens_used": SETTINGS.token_budget,
             "evidence": []},
            {},
        )
    assert result["convergence_reason"] == "budget"
    assert result["refused"] is True
    assert events[0]["delta"] == BUDGET_DEGRADED_TEXT


async def test_chain_timeout_degrades():
    """熔断超时（章程 IV）：asyncio.timeout 在预算内确定性中止。"""
    import asyncio

    from src.agent.prompts import DEGRADED_TEXT

    async def slow_stream(messages):
        await asyncio.sleep(10)  # 远超熔断线
        yield "never"

    llm = FakeLLM()
    llm.stream_chat = slow_stream  # type: ignore[method-assign]
    node = make_generate_node(llm, SETTINGS)
    events: list[dict] = []
    with patch("src.agent.nodes.generate.get_stream_writer", return_value=events.append):
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.1):
                await node(
                    {
                        "question": "q",
                        "route": "retrieve",
                        "evidence": [{"hits": [{"n": 1, "doc_id": "d", "title": "t",
                                                "sec_no": None, "score": 0.9,
                                                "parent_text": "p"}]}],
                    },
                    {},
                )
    # 编排层捕获 TimeoutError 后以 DEGRADED_TEXT 收敛（见 tests/contract/test_chat_convergence.py）
    assert DEGRADED_TEXT
