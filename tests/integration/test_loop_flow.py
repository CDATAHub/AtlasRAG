"""T025：反思回环集成（真实 PG + 脚本化回环），档案 plan_rounds 与事件一致。"""

import json

import pytest
from sqlalchemy import select

from src.data.models import RuntimeLog
from tests.conftest import auth, build_client, parse_sse
from tests.unit.fakes import FakeLLM

pytestmark = pytest.mark.asyncio

REFLECT = json.dumps(
    {"sufficient": False, "reason": "首轮未命中", "next_action": "rewrite_query",
     "next_query": "重疾险 等待期"},
    ensure_ascii=False,
)


async def test_loop_repair_and_archive(seeded_lib, db, token):
    llm = FakeLLM(
        chat_responses=[
            json.dumps({"route": "retrieve",
                        "plan": [{"step": 1, "action": "retrieve", "tool": "hybrid_search",
                                  "query": "保险 合同 生效", "rationale": "r"}]}, ensure_ascii=False),
            REFLECT,
            json.dumps({"route": "retrieve",
                        "plan": [{"step": 1, "action": "retrieve", "tool": "hybrid_search",
                                  "query": "重疾险 等待期", "rationale": "改写"}]}, ensure_ascii=False),
        ],
        stream_scripts=[["不确定[1]。"], ["等待期为 90 日[1]。"]],
    )
    client = build_client(db, embedding=seeded_lib, llm=llm)
    try:
        resp = await client.post(
            "/v1/chat", json={"question": "保险合同什么时候生效？"}, headers=auth(token)
        )
        assert resp.status_code == 200
        events = parse_sse(resp.text)
        done = events[-1][1]
        assert done["rounds"] == 2 and done["refused"] is False

        async with client.app_state.session_factory() as s:
            log = (await s.scalars(select(RuntimeLog))).one()
        assert log.plan_rounds == done["rounds"]  # 档案与事件一致（FR-016）
        assert log.steps == 2
        assert log.convergence_reason == "natural"
    finally:
        await client.aclose()
