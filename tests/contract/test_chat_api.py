"""T017：chat 接口契约——SSE 事件序、401/422/503 错误路径。"""

import pytest

from tests.conftest import auth, parse_sse

pytestmark = pytest.mark.asyncio


async def test_missing_token_401(seed_waiting_clause):
    client = seed_waiting_clause
    resp = await client.post("/v1/chat", json={"question": "等待期？"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


async def test_bad_token_401(seed_waiting_clause):
    client = seed_waiting_clause
    resp = await client.post(
        "/v1/chat", json={"question": "等待期？"}, headers={"Authorization": "Bearer bad.token"}
    )
    assert resp.status_code == 401


async def test_empty_question_422(seed_waiting_clause, token):
    client = seed_waiting_clause
    resp = await client.post("/v1/chat", json={"question": "   "}, headers=auth(token))
    assert resp.status_code == 422


async def test_overlong_question_422(seed_waiting_clause, token):
    client = seed_waiting_clause
    resp = await client.post("/v1/chat", json={"question": "等" * 501}, headers=auth(token))
    assert resp.status_code == 422


async def test_empty_library_503(db, token):
    """FR-010：条款库为空 → 503 明确提示，而非空白或错误堆栈。"""
    from httpx import ASGITransport, AsyncClient

    from tests.conftest import make_app
    from tests.unit.fakes import FakeEmbedding, FakeLLM, FakeRerank

    app = make_app(db, embedding=FakeEmbedding(), rerank=FakeRerank(), llm=FakeLLM())
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    resp = await client.post("/v1/chat", json={"question": "等待期？"}, headers=auth(token))
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "service_unavailable"
    assert "暂不可用" in body["message"]
    await client.aclose()


async def test_sse_event_order_and_contract(seed_waiting_clause, token):
    """002 契约：plan → tool_call → evidence → answer* → citations → done。"""
    client = seed_waiting_clause
    resp = await client.post(
        "/v1/chat", json={"question": "这款重疾险等待期多久？"}, headers=auth(token)
    )
    events = parse_sse(resp.text)
    names = [name for name, _ in events]

    assert names[0] == "plan"  # 阶段 2：先规划（FakeLLM 默认 {} → 降级单步检索）
    assert names.count("tool_call") == names.count("evidence") >= 1
    assert names[-2:] == ["citations", "done"]
    assert names.index("citations") > names.index("evidence")

    evidence = next(p for n, p in events if n == "evidence")
    assert evidence["trace_id"] == events[-1][1]["trace_id"]  # 全链路同一 trace
    for hit in evidence["hits"]:
        assert set(hit) >= {"n", "doc_id", "title", "score"}

    done = events[-1][1]
    assert set(done) >= {
        "trace_id", "latency_ms", "refused", "hit_count", "top_score",
        "session_id", "message_id", "client_msg_id", "convergence_reason",
        "rounds", "steps", "tokens_used",
    }
    for _, payload in events:
        assert payload.get("trace_id", done["trace_id"]) == done["trace_id"]  # 每事件带 trace
