"""T034：拒答路径（US3）——低分强制拒答，runtime_log.refused=true。"""

import pytest
from sqlalchemy import select

from src.data.models import RuntimeLog
from tests.conftest import auth, parse_sse
from tests.unit.fakes import FakeRerank


@pytest.mark.asyncio
async def test_low_score_forces_refusal(seed_waiting_clause, token):
    """编程式低分 FakeRerank → 拒答：answer 单条建议 + citations 空 + done refused。"""
    client = seed_waiting_clause
    # 替换应用内的 rerank 为低分版本（低于阈值 0.35）
    client.app_state.rerank = FakeRerank(scores=[0.1] * 10)

    resp = await client.post(
        "/v1/chat", json={"question": "等待期多久？"}, headers=auth(token)
    )
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    names = [name for name, _ in events]

    assert names == ["answer", "citations", "done"]  # 拒答无 evidence 事件（契约）
    answer_text = events[0][1]["delta"]
    assert "不作推测" in answer_text and "等待期" in answer_text  # 建议文案（FR-008）
    assert events[1][1]["citations"] == []
    done = events[2][1]
    assert done["refused"] is True

    session_factory = client.app_state.session_factory
    async with session_factory() as session:
        logs = (await session.scalars(select(RuntimeLog))).all()
    assert logs and logs[0].refused is True
