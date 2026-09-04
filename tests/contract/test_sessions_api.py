"""T031：会话管理契约——历史/删除/404/409/幂等重放（US4）。"""

import pytest

from src.services import sessions as sessions_svc
from tests.conftest import auth, other_token, parse_sse  # noqa: F401
from tests.conftest import auth as _auth

pytestmark = pytest.mark.asyncio


async def _ask(client, token, question, **extra):
    return await client.post(
        "/v1/chat", json={"question": question, **extra}, headers=_auth(token)
    )


async def test_history_query_and_delete(seed_waiting_clause, token):
    client = seed_waiting_clause
    resp = await _ask(client, token, "这款重疾险等待期多久？", client_msg_id="h-1")
    assert resp.status_code == 200
    session_id = parse_sse(resp.text)[-1][1]["session_id"]

    resp = await client.get(f"/v1/sessions/{session_id}", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][1]["citations"]  # 历史含引用（FR-010）

    resp = await client.delete(f"/v1/sessions/{session_id}", headers=_auth(token))
    assert resp.status_code == 204
    resp = await client.get(f"/v1/sessions/{session_id}", headers=_auth(token))
    assert resp.status_code == 404  # 删除后不可查询（FR-010）


async def test_cross_tenant_and_unknown_session_404(seed_waiting_clause, token, other_token):
    client = seed_waiting_clause
    resp = await _ask(client, token, "等待期多久？")
    session_id = parse_sse(resp.text)[-1][1]["session_id"]

    resp = await client.get(f"/v1/sessions/{session_id}", headers=_auth(other_token))
    assert resp.status_code == 404  # 跨租户不可见（FR-017）
    resp = await client.get("/v1/sessions/00000000-0000-0000-0000-000000000000", headers=_auth(token))
    assert resp.status_code == 404


async def test_session_busy_409(seed_waiting_clause, token):
    """FR-012：同会话串行——锁被占用时新请求 409。"""
    client = seed_waiting_clause
    resp = await _ask(client, token, "等待期多久？")
    session_id = parse_sse(resp.text)[-1][1]["session_id"]

    import uuid as _uuid

    lock = sessions_svc.lock_for(_uuid.UUID(session_id))
    async with lock:  # 模拟进行中的请求
        resp = await _ask(client, token, "宽限期呢？", session_id=session_id)
        assert resp.status_code == 409
        assert resp.json()["code"] == "session_busy"


async def test_idempotent_replay(seed_waiting_clause, token):
    """FR-013：同会话同 client_msg_id 重放既有事件流，不重复生成、无新档案行。"""
    client = seed_waiting_clause
    resp = await _ask(client, token, "这款重疾险等待期多久？", client_msg_id="idem-1")
    events = parse_sse(resp.text)
    done = events[-1][1]
    session_id = done["session_id"]
    answer_text = "".join(p["delta"] for n, p in events if n == "answer")

    resp2 = await _ask(
        client, token, "这款重疾险等待期多久？", session_id=session_id, client_msg_id="idem-1"
    )
    assert resp2.status_code == 200
    events2 = parse_sse(resp2.text)
    assert [n for n, _ in events2] == ["answer", "citations", "done"]  # 重放事件流
    assert "".join(p["delta"] for n, p in events2 if n == "answer") == answer_text
    assert events2[-1][1]["replayed"] is True
    assert events2[-1][1]["trace_id"] == done["trace_id"]  # 同一次运行，未重复生成

    from sqlalchemy import select

    from src.data.models import RuntimeLog
    async with client.app_state.session_factory() as s:
        logs = (await s.scalars(select(RuntimeLog))).all()
    assert len(logs) == 1  # 无新档案行
