"""T038：检查点恢复（US5 / clarify Q2）——崩溃后续跑同一次回答，已完成步骤不重做。"""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

import pytest

from src.data import dao
from src.data.models import Message
from tests.conftest import TENANT, auth, build_client, parse_sse
from tests.unit.fakes import FakeLLM

pytestmark = pytest.mark.asyncio


def _plan_script(query: str) -> str:
    return json.dumps(
        {"route": "retrieve",
         "plan": [{"step": 1, "action": "retrieve", "tool": "hybrid_search",
                   "query": query, "rationale": "r"}]},
        ensure_ascii=False,
    )


class SlowStreamLLM(FakeLLM):
    """generate 阶段慢速输出：为测试制造可取消的执行窗口。"""

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        self.calls.append(messages)
        deltas = self.stream_scripts.pop(0) if self.stream_scripts else self.deltas
        for delta in deltas:
            await asyncio.sleep(1.0)
            yield delta


async def test_resume_after_crash(seeded_lib, db, token, pg_checkpointer):
    """US5 场景 1：图执行中崩溃 → 新进程实例带相同幂等键重发 → 续跑不重做。"""
    question = "这款重疾险等待期多久？"
    llm = SlowStreamLLM(
        chat_responses=[
            _plan_script("等待期 定义"),
            json.dumps({"sufficient": True, "next_action": "converge"}),
            json.dumps({"sufficient": True, "next_action": "converge"}),
        ],
        stream_scripts=[["等待期为 90 日[1]。"], ["（恢复后）等待期为 90 日[1]。"]],
    )
    client_a = build_client(db, embedding=seeded_lib, llm=llm, checkpointer=pg_checkpointer)
    try:
        # 1) 会话 + 提问行（模拟客户端已提交）
        import uuid as _uuid

        async with db() as s:
            row = await dao.create_session(s, TENANT, title="resume")
            await s.commit()
            session_id = row.id
            await dao.append_message(
                s, Message(session_id=session_id, tenant_id=TENANT,
                           client_msg_id="resume-1", role="user", content=question)
            )
            await s.commit()

        # 2) 模拟执行到 generate 阶段崩溃（取消任务；检查点已落 PG）
        graph = client_a.app_state.graph
        config = {
            "configurable": {
                "thread_id": f"{session_id}:resume-1",
                "db": None,  # ainvoke 前工具未执行，崩溃点在 plan/tool 之后
            }
        }
        async with db() as s:
            config["configurable"]["db"] = s
            task = asyncio.create_task(
                graph.ainvoke(
                    {
                        "question": question, "tenant_id": TENANT, "trace_id": "tr-resume",
                        "session_id": str(session_id), "message_id": "m", "client_msg_id": "resume-1",
                        "history_text": "", "tokens_used": 0,
                        "messages": [{"role": "user", "content": question}],
                    },
                    config,
                )
            )
            await asyncio.sleep(0.3)  # plan+tool 完成，generate 睡眠中（1s 窗口内）
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        # 3) 「重启」：新 app 实例（同一测试 PG 检查点器）重发相同幂等键
        client_b = build_client(db, embedding=seeded_lib, llm=llm, checkpointer=pg_checkpointer)
        try:
            resp = await client_b.post(
                "/v1/chat",
                json={"question": question, "session_id": str(session_id),
                      "client_msg_id": "resume-1"},
                headers=auth(token),
            )
            assert resp.status_code == 200
            events = parse_sse(resp.text)
            names = [n for n, _ in events]

            assert "plan" not in names and "tool_call" not in names  # 已完成步骤不重做
            assert names == ["answer", "citations", "done"]
            done = events[-1][1]
            assert done["refused"] is False
            assert done["client_msg_id"] == "resume-1"
        finally:
            await client_b.aclose()
    finally:
        await client_a.aclose()


async def test_resume_without_checkpoint_degrades(seeded_lib, db, token, pg_checkpointer):
    """US5 场景 2：检查点不可用 → 明确错误与重新开始指引，无半截答案。"""
    client = build_client(db, embedding=seeded_lib, llm=FakeLLM(), checkpointer=pg_checkpointer)
    try:
        import uuid as _uuid

        async with db() as s:
            row = await dao.create_session(s, TENANT, title="broken")
            await s.commit()
            session_id = row.id
            await dao.append_message(
                s, Message(session_id=session_id, tenant_id=TENANT,
                           client_msg_id="broken-1", role="user", content="等待期多久？")
            )
            await s.commit()

        resp = await client.post(
            "/v1/chat",
            json={"question": "等待期多久？", "session_id": str(session_id),
                  "client_msg_id": "broken-1"},
            headers=auth(token),
        )
        assert resp.status_code == 200
        events = parse_sse(resp.text)
        names = [n for n, _ in events]
        assert names == ["answer", "citations", "done"]  # 完整事件面，无半截
        assert "不可用" in events[0][1]["delta"] or "重新开始" in events[0][1]["delta"]
        done = events[-1][1]
        assert done["refused"] is True
    finally:
        await client.aclose()
