"""T034：拒答 SSE 契约——事件序 answer→citations(空)→done(refused=true)。"""

import pytest

from tests.conftest import auth, parse_sse
from tests.unit.fakes import FakeRerank

pytestmark = pytest.mark.asyncio


async def test_refusal_sse_contract(seeded_lib, db, token):
    """002 契约拒答路径：工具轨迹照常外发，低分 → answer 拒答 + 空 citations。"""
    from tests.conftest import build_client

    client = build_client(db, embedding=seeded_lib, rerank=FakeRerank(scores=[0.1] * 10))
    try:
        resp = await client.post("/v1/chat", json={"question": "等待期？"}, headers=auth(token))
        events = parse_sse(resp.text)
        names = [name for name, _ in events]

        assert names == ["plan", "tool_call", "evidence", "answer", "citations", "done"]
        assert "不作推测" in events[-3][1]["delta"]
        assert events[-2][1] == {"citations": []}
        done = events[-1][1]
        assert done["refused"] is True
        assert done["top_score"] is not None and done["top_score"] < 0.35
        assert done["convergence_reason"] == "refused"
    finally:
        await client.aclose()
