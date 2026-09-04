"""T036/T037：多轮会话集成——指代消解机制回归（50 条）+ 压缩后引用保真。"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.agent.nodes.plan import make_plan_node
from src.config import get_settings
from src.data import dao
from src.data.models import Message
from src.services import context_window
from src.tools.base import Registry
from tests.conftest import TENANT, auth, build_client, parse_sse
from tests.unit.fakes import FakeLLM

pytestmark = pytest.mark.asyncio

SETTINGS = get_settings()
CASES = [
    json.loads(l)
    for l in (Path(__file__).parent.parent / "fixtures" / "multi_turn.jsonl").read_text().splitlines()
    if l.strip()
]


def _plan_script(query: str) -> str:
    return json.dumps(
        {"route": "retrieve",
         "plan": [{"step": 1, "action": "retrieve", "tool": "hybrid_search",
                   "query": query, "rationale": "r"}]},
        ensure_ascii=False,
    )


async def test_multi_turn_resolution_mechanism():
    """T036（SC-006 机制回归）：50 条双轮用例——追问时首问关键词必须进入规划器。"""
    for case in CASES:
        history = [
            {"role": "user", "content": case["first_q"], "citations": []},
            {"role": "assistant", "content": case["first_a"],
             "citations": case["first_citations"]},
        ]
        context, _ = await context_window.build_context(history, SETTINGS)
        llm = FakeLLM(chat_responses=[_plan_script(f"{case['expect_keyword']} 改写检索式")])
        node = make_plan_node(llm, SETTINGS, Registry())
        with patch("src.agent.nodes.plan.get_stream_writer", return_value=lambda _: None):
            await node(
                {"question": case["followup_q"], "history_text": context,
                 "messages": [{"role": "user", "content": case["followup_q"]}]},
                {},
            )
        prompt = llm.chat_calls[0][1]["content"]
        assert case["expect_keyword"] in prompt, (
            f"{case['id']}：追问的规划 prompt 未包含首问关键词（指代消解依赖失败）"
        )


async def test_followup_hits_and_history_persisted(seeded_lib, db, token):
    """T037：真实两轮——追问的回答引用正确条款，历史接口可查全两轮。"""
    llm = FakeLLM(
        chat_responses=[
            _plan_script("等待期 定义"),
            json.dumps({"sufficient": True, "next_action": "converge"}),  # 轮 1 反思
            _plan_script("等待期 起算 合同"),
            json.dumps({"sufficient": True, "next_action": "converge"}),  # 轮 2 反思
        ],
        stream_scripts=[["等待期为 90 日[1]。"], ["宽限期为 60 日[1]。"]],
    )
    client = build_client(db, embedding=seeded_lib, llm=llm)
    try:
        resp = await client.post(
            "/v1/chat", json={"question": "康护一生的等待期是多久？"}, headers=auth(token)
        )
        session_id = parse_sse(resp.text)[-1][1]["session_id"]

        resp = await client.post(
            "/v1/chat",
            json={"question": "那它的宽限期呢？", "session_id": session_id},
            headers=auth(token),
        )
        assert resp.status_code == 200
        events = parse_sse(resp.text)
        done = events[-1][1]
        assert done["refused"] is False
        citations = events[-2][1]["citations"]
        assert citations and citations[0]["quote"]  # 追问回答带可展开引用

        # 规划器在追问时拿到了首问历史（指代消解输入）：chat 序列 = plan1/reflect1/plan2/reflect2
        plan_prompt = llm.chat_calls[2][1]["content"]
        assert "康护一生" in plan_prompt and "宽限期" in plan_prompt

        resp = await client.get(f"/v1/sessions/{session_id}", headers=auth(token))
        body = resp.json()
        assert len(body["messages"]) == 4  # 两问两答
    finally:
        await client.aclose()


async def test_compressed_history_keeps_citations(seeded_lib, db, token):
    """T037 / SC-007（mock 化）：超长历史压缩后，追问的回答引用仍可展开原文。"""
    llm = FakeLLM(
        chat_responses=[
            json.dumps({"summary": "用户此前询问康护一生等待期（已引用 康护一生条款 2.3.1）。"},
                       ensure_ascii=False),  # 摘要调用（context_window summarizer）
            _plan_script("等待期 起算 合同"),
        ],
        stream_scripts=[["宽限期为 60 日[1]。"]],
    )
    client = build_client(db, embedding=seeded_lib, llm=llm)
    try:
        # 直播种超长历史（绕过上传），触发压缩阈值
        async with db() as s:
            row = await dao.create_session(s, TENANT, title="长会话")
            await s.commit()
            session_id = row.id
            long_history = [
                Message(session_id=session_id, tenant_id=TENANT, client_msg_id=f"seed-{i}",
                        role="user", content="请详细解释条款细节问题" * 40)
                for i in range(30)
            ] + [
                Message(session_id=session_id, tenant_id=TENANT, client_msg_id=f"seed-a-{i}",
                        role="assistant", content="条款规定如下" * 40,
                        citations=[{"n": 1, "title": "康护一生条款", "sec_no": "2.3.1"}])
                for i in range(30)
            ]
            s.add_all(long_history)
            await s.commit()

        resp = await client.post(
            "/v1/chat",
            json={"question": "那它的宽限期呢？", "session_id": str(session_id)},
            headers=auth(token),
        )
        assert resp.status_code == 200
        events = parse_sse(resp.text)
        assert events[-1][1]["refused"] is False
        citations = events[-2][1]["citations"]
        assert citations and citations[0]["quote"]  # 压缩后引用仍可展开（FR-014）

        # 摘要输入必须包含历史引用出处（证据链不压缩）
        summary_prompt = llm.chat_calls[0][1]["content"]
        assert "康护一生条款" in summary_prompt
    finally:
        await client.aclose()
