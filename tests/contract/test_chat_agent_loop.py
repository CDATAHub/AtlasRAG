"""T010：chat v2 AgentLoop 契约——事件序、done 扩展字段、寒暄快路径、直答路径。"""

import json

import pytest

from tests.conftest import auth, build_client, parse_sse
from tests.unit.fakes import FakeLLM
from tests.unit.test_plan_route import TWO_STEP_PLAN

pytestmark = pytest.mark.asyncio


def _plan_script(plan: dict) -> list[str]:
    return [json.dumps(plan, ensure_ascii=False)]


async def test_two_step_plan_events(seeded_lib, db, token):
    """US1 场景 1：plan.steps≥2 → 逐步 tool_call/evidence → 答案带引用。"""
    llm = FakeLLM(
        deltas=["等待期为 90 日[1]。等待期内出险不承担给付责任[2]。"],
        chat_responses=_plan_script(TWO_STEP_PLAN),
    )
    client = build_client(db, embedding=seeded_lib, llm=llm)
    try:
        resp = await client.post(
            "/v1/chat",
            json={"question": "这款重疾险等待期多久？等待期内出险赔吗？", "client_msg_id": "c-1"},
            headers=auth(token),
        )
        assert resp.status_code == 200
        events = parse_sse(resp.text)
        names = [n for n, _ in events]

        assert names == [
            "plan", "tool_call", "evidence", "tool_call", "evidence",
            "answer", "citations", "done",
        ]
        plan = events[0][1]
        assert plan["round"] == 1 and len(plan["steps"]) == 2
        assert plan["steps"][0]["query"] == "重疾险 等待期"
        assert plan["session_id"] and plan["message_id"]

        assert events[1][1]["tool"] == "hybrid_search"
        assert events[2][1]["round"] == 1 and events[2][1]["hits"]

        done = events[-1][1]
        assert done["steps"] == 2 and done["rounds"] == 1
        assert done["refused"] is False
        assert done["convergence_reason"] == "natural"
        assert done["client_msg_id"] == "c-1"
        assert set(done) >= {
            "trace_id", "session_id", "message_id", "latency_ms",
            "hit_count", "top_score", "tokens_used",
        }
        citations = events[-2][1]["citations"]
        assert citations  # 非拒答必有引用（FR-006）
    finally:
        await client.aclose()


async def test_chitchat_fast_path(seeded_lib, db, token):
    """US1 场景 3：寒暄走模板快路径——零 LLM/检索，tokens_used=0。"""
    llm = FakeLLM()
    client = build_client(db, embedding=seeded_lib, llm=llm)
    try:
        resp = await client.post(
            "/v1/chat", json={"question": "你好"}, headers=auth(token)
        )
        assert resp.status_code == 200
        events = parse_sse(resp.text)
        assert [n for n, _ in events] == ["answer", "citations", "done"]  # 无 plan/tool_call
        done = events[-1][1]
        assert done["refused"] is False
        assert done["tokens_used"] == 0 and done["rounds"] == 0 and done["steps"] == 0
        assert llm.chat_calls == [] and llm.calls == []  # 零 LLM 调用（research D8）
    finally:
        await client.aclose()


async def test_direct_answer_route(seeded_lib, db, token):
    """US1 场景 4：plan 判定直答 → 无工具调用（done.steps=0），LLM 直答。"""
    llm = FakeLLM(
        deltas=["保险是转移风险的合同安排。"],
        chat_responses=[json.dumps({"route": "answer", "plan": []})],
    )
    client = build_client(db, embedding=seeded_lib, llm=llm)
    try:
        resp = await client.post(
            "/v1/chat", json={"question": "保险是什么？"}, headers=auth(token)
        )
        events = parse_sse(resp.text)
        names = [n for n, _ in events]
        assert names == ["plan", "answer", "citations", "done"]  # 无 tool_call/evidence
        done = events[-1][1]
        assert done["steps"] == 0 and done["refused"] is False
    finally:
        await client.aclose()


async def test_plan_parse_failure_degrades_to_single_step(seeded_lib, db, token):
    """spec Edge：规划输出不可解析 → 重试 1 次后降级单步检索，服务不中断。"""
    llm = FakeLLM(deltas=["等待期为 90 日[1]。"], chat_responses=["不是JSON", "也不是JSON"])
    client = build_client(db, embedding=seeded_lib, llm=llm)
    try:
        resp = await client.post(
            "/v1/chat", json={"question": "等待期多久？"}, headers=auth(token)
        )
        events = parse_sse(resp.text)
        names = [n for n, _ in events]
        assert names[0] == "plan"
        plan = events[0][1]
        assert len(plan["steps"]) == 1  # 降级单步
        assert plan["steps"][0]["query"] == "等待期多久？"  # 检索式回退原问题
        assert names[-1] == "done" and events[-1][1]["refused"] is False
    finally:
        await client.aclose()


async def test_session_not_found_404(seeded_lib, db, token):
    """契约 §1：session_id 不存在 → 404（不泄露存在性）。"""
    client = build_client(db, embedding=seeded_lib)
    try:
        resp = await client.post(
            "/v1/chat",
            json={"question": "等待期？", "session_id": "00000000-0000-0000-0000-000000000000"},
            headers=auth(token),
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "session_not_found"
    finally:
        await client.aclose()
