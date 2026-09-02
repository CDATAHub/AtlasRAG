"""T034：拒答 SSE 契约——事件序 answer→citations(空)→done(refused=true)。"""

import pytest

from tests.conftest import auth, parse_sse
from tests.unit.fakes import FakeRerank

pytestmark = pytest.mark.asyncio


async def test_refusal_sse_contract(seed_waiting_clause, token):
    client = seed_waiting_clause
    client.app_state.rerank = FakeRerank(scores=[0.1] * 10)  # 低于阈值 0.35

    resp = await client.post("/v1/chat", json={"question": "等待期？"}, headers=auth(token))
    events = parse_sse(resp.text)
    names = [name for name, _ in events]

    assert names == ["answer", "citations", "done"]  # 无 evidence 事件（契约 §3 拒答路径）
    assert "不作推测" in events[0][1]["delta"]
    assert events[1][1] == {"citations": []}
    done = events[2][1]
    assert done["refused"] is True
    assert done["top_score"] is not None and done["top_score"] < 0.35
