"""T027：收敛保险契约——步数上限、预算耗尽、熔断超时的 SSE 事件面。"""

import json

import pytest

from tests.conftest import auth, build_client, parse_sse
from tests.unit.fakes import FakeLLM

pytestmark = pytest.mark.asyncio


def _plan(queries: list[str]) -> str:
    return json.dumps(
        {"route": "retrieve",
         "plan": [{"step": i + 1, "action": "retrieve", "tool": "hybrid_search",
                   "query": q, "rationale": "r"} for i, q in enumerate(queries)]},
        ensure_ascii=False,
    )


async def test_max_steps_forces_convergence(seeded_lib, db, token):
    """US3 场景 1：6 步计划执行完 → 强制收敛，convergence_reason=max_steps。"""
    llm = FakeLLM(chat_responses=[_plan(["条款 保险 合同"] * 6)])
    client = build_client(db, embedding=seeded_lib, llm=llm)
    try:
        resp = await client.post(
            "/v1/chat", json={"question": "保险合同什么时候生效？"}, headers=auth(token)
        )
        events = parse_sse(resp.text)
        done = events[-1][1]
        assert done["steps"] == 6  # 恰好步数上限
        assert done["convergence_reason"] == "max_steps"
        assert done["refused"] is False  # 有草稿则输出草稿（FR-007 输出可用结果）
    finally:
        await client.aclose()


async def test_budget_exhaustion_degrades(seeded_lib, db, token):
    """US3 场景 3：单次 LLM 调用耗尽预算 → 降级 done（budget），不编造。"""
    llm = FakeLLM(deltas=["等待期为 90 日[1]。"], usage_tokens=8000)
    client = build_client(db, embedding=seeded_lib, llm=llm)
    try:
        resp = await client.post(
            "/v1/chat", json={"question": "保险合同什么时候生效？"}, headers=auth(token)
        )
        events = parse_sse(resp.text)
        names = [n for n, _ in events]
        done = events[-1][1]

        assert names == ["plan", "tool_call", "evidence", "answer", "citations", "done"]
        assert done["convergence_reason"] == "budget"
        assert done["refused"] is True
        assert events[-3][1]["delta"]  # 降级提示已输出（非空白）
        assert done["tokens_used"] >= 8000  # 档案记录实际用量
    finally:
        await client.aclose()


async def test_timeout_degrades_without_hanging(seeded_lib, db, token):
    """US3 场景 2：链路缓慢 → 熔断降级 done（timeout），连接不断。"""
    import asyncio

    async def slow_stream(messages):
        await asyncio.sleep(5)
        yield "太慢"

    llm = FakeLLM(chat_responses=[_plan(["保险 合同 生效"])])
    llm.stream_chat = slow_stream  # type: ignore[method-assign]
    client = build_client(
        db, embedding=seeded_lib, llm=llm, settings_overrides={"chain_timeout_s": 0.3}
    )
    try:
        resp = await client.post(
            "/v1/chat", json={"question": "保险合同什么时候生效？"}, headers=auth(token)
        )
        assert resp.status_code == 200  # 不断连，流内降级（FR-008）
        events = parse_sse(resp.text)
        done = events[-1][1]
        assert done["convergence_reason"] == "timeout"
        assert done["refused"] is True
    finally:
        await client.aclose()
