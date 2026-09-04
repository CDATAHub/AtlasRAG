"""T019：AgentLoop 全链路集成（真实 PG + fake 客户端 + MemorySaver 检查点）。"""

import json

import pytest
from sqlalchemy import select

from src.data.models import Message, RuntimeLog, Session
from tests.conftest import auth, build_client, parse_sse
from tests.unit.fakes import FakeLLM

pytestmark = pytest.mark.asyncio

PLAN = {
    "route": "retrieve",
    "plan": [
        {"step": 1, "action": "retrieve", "tool": "hybrid_search",
         "query": "重疾险 等待期", "rationale": "等待期定义"},
        {"step": 2, "action": "retrieve", "tool": "hybrid_search",
         "query": "等待期 出险 责任", "rationale": "等待期内出险责任"},
    ],
}


async def test_multi_subtask_flow(seeded_lib, db, token):
    """US1 独立测试：两步计划逐步执行，答案带引用，档案与会话落库。"""
    llm = FakeLLM(
        deltas=["等待期为 90 日[1]；等待期内出险不赔[2]。"],
        chat_responses=[json.dumps(PLAN, ensure_ascii=False)],
    )
    client = build_client(db, embedding=seeded_lib, llm=llm)
    try:
        resp = await client.post(
            "/v1/chat",
            json={"question": "这款重疾险等待期多久？等待期内出险赔吗？"},
            headers=auth(token),
        )
        assert resp.status_code == 200
        events = parse_sse(resp.text)
        names = [n for n, _ in events]

        assert names[0] == "plan" and names.count("tool_call") == 2
        assert names[-2:] == ["citations", "done"]
        done = events[-1][1]
        assert done["steps"] == 2 and done["rounds"] == 1
        assert done["refused"] is False and done["hit_count"] >= 1

        answer_text = "".join(p["delta"] for n, p in events if n == "answer")
        assert "90 日" in answer_text

        # 运行档案扩展列（FR-016）
        session_factory = client.app_state.session_factory
        async with session_factory() as s:
            log = (await s.scalars(select(RuntimeLog))).one()
        assert log.plan_rounds == 1 and log.steps == 2
        assert log.convergence_reason == "natural"
        assert log.tokens_used > 0  # plan 的 usage 记账
        assert log.session_id is not None and log.message_id is not None

        # 会话与消息持久化（FR-009/010 最小写入）
        async with session_factory() as s:
            sess = (await s.scalars(select(Session))).one()
            msgs = (await s.scalars(select(Message).order_by(Message.created_at))).all()
        assert sess.status == "idle"
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[1].citations and msgs[1].trace_id == done["trace_id"]
    finally:
        await client.aclose()
