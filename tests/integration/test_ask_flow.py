"""T018：提问→回答全链路（真实 PG + fake 客户端）。

含口语化提问用例（FR-005，analyze E2 修复项）。
"""

import pytest

from src.data import dao
from src.data.models import RuntimeLog
from tests.conftest import TENANT, auth, parse_sse


@pytest.mark.asyncio
async def test_ask_returns_cited_answer(seed_waiting_clause, token):
    client = seed_waiting_clause
    resp = await client.post("/v1/chat", json={"question": "这款重疾险等待期多久？"}, headers=auth(token))
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    names = [name for name, _ in events]

    assert names[0] == "evidence"  # 事件序：evidence → answer* → citations → done
    assert names.count("answer") >= 1
    assert names[-2:] == ["citations", "done"]

    done = events[-1][1]
    assert done["refused"] is False
    assert done["hit_count"] >= 1
    assert done["trace_id"].startswith("tr-")

    citations = events[-2][1]["citations"]
    answer_text = "".join(p["delta"] for _, p in events if _ and "delta" in p)
    assert citations, "非拒答回答必须带引用（FR-006）"
    for citation in citations:
        assert citation["quote"]  # 引用含条款原文（FR-007）

    # [n] 与 citations 一一对应（FR-006）
    import re

    refs = set(re.findall(r"\[(\d+)\]", answer_text))
    assert refs <= {str(c["n"]) for c in citations}


@pytest.mark.asyncio
async def test_colloquial_question_hits_waiting_period(seed_waiting_clause, token):
    """口语化说法（FR-005）：「多久才开始生效能赔」→ 命中等待期条款。"""
    client = seed_waiting_clause
    resp = await client.post(
        "/v1/chat", json={"question": "买的保险多久才开始生效能赔？"}, headers=auth(token)
    )
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    done = events[-1][1]
    if done["refused"]:
        pytest.fail("口语化提问被拒答，语义/关键词召回未覆盖（FR-005）")
    evidence = events[0][1]["hits"]
    titles = " ".join(h["title"] for h in evidence)
    assert "康护一生" in titles or "等待期" in titles


@pytest.mark.asyncio
async def test_runtime_log_written(seed_waiting_clause, token):
    client = seed_waiting_clause
    await client.post("/v1/chat", json={"question": "等待期是多久？"}, headers=auth(token))
    session_factory = client.app_state.session_factory
    async with session_factory() as session:
        logs = (await session.scalars(query := __import__("sqlalchemy").select(RuntimeLog))).all()
    assert len(logs) == 1
    assert logs[0].question == "等待期是多久？"
    assert logs[0].trace_id.startswith("tr-")
    assert logs[0].refused is False
