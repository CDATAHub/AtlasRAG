"""T021：反思回环契约——round 递增、已执行步骤不重跑、3 轮上限强制收敛。"""

import json

import pytest

from tests.conftest import auth, build_client, parse_sse
from tests.unit.fakes import FakeLLM

pytestmark = pytest.mark.asyncio

REFLECT_INSUFFICIENT = json.dumps(
    {"sufficient": False, "reason": "未覆盖等待期", "next_action": "rewrite_query",
     "next_query": "重疾险 等待期"},
    ensure_ascii=False,
)


def _plan(queries: list[str]) -> str:
    return json.dumps(
        {"route": "retrieve",
         "plan": [{"step": i + 1, "action": "retrieve", "tool": "hybrid_search",
                   "query": q, "rationale": "r"} for i, q in enumerate(queries)]},
        ensure_ascii=False,
    )


async def test_loop_round2_replan(seeded_lib, db, token):
    """US2 场景 1+2：首轮不足 → 改写补检（round=2），已执行步骤不重复。"""
    llm = FakeLLM(
        chat_responses=[
            _plan(["保险 生效"]),           # 轮 1 计划（弱检索式）
            REFLECT_INSUFFICIENT,           # 轮 1 反思：改写
            _plan(["重疾险 等待期"]),        # 轮 2 计划（改写检索式）
        ],
        stream_scripts=[
            ["证据不足，无法回答。"],        # 轮 1 生成
            ["等待期为 90 日[1]。"],         # 轮 2 生成
        ],
    )
    client = build_client(db, embedding=seeded_lib, llm=llm)
    try:
        resp = await client.post(
            "/v1/chat", json={"question": "买的保险多久能赔？"}, headers=auth(token)
        )
        assert resp.status_code == 200
        events = parse_sse(resp.text)
        names = [n for n, _ in events]

        assert names == [
            "plan", "tool_call", "evidence", "answer",       # 轮 1
            "plan", "tool_call", "evidence", "answer",       # 轮 2
            "citations", "done",
        ]
        plans = [p for n, p in events if n == "plan"]
        assert plans[0]["round"] == 1 and plans[1]["round"] == 2
        tool_calls = [p for n, p in events if n == "tool_call"]
        assert [t["query"] for t in tool_calls] == ["保险 生效", "重疾险 等待期"]  # 前缀不重跑

        done = events[-1][1]
        assert done["rounds"] == 2 and done["steps"] == 2
        assert done["refused"] is False and done["convergence_reason"] == "natural"
    finally:
        await client.aclose()


async def test_loop_three_round_cap(seeded_lib, db, token):
    """US2 场景 3：反思持续不足 → 第 3 轮后强制收敛（FR-006）。"""
    llm = FakeLLM(
        chat_responses=[
            _plan(["保险 生效"]),
            REFLECT_INSUFFICIENT,
            _plan(["保险 保险责任 条款"]),
            REFLECT_INSUFFICIENT,
            _plan(["等待期 定义 90日"]),   # 轮 3 计划；此后反思触发硬上限
        ],
        stream_scripts=[["信息不足。"], ["仍不足。"], ["依据条款[1]等待期为 90 日。"]],
    )
    client = build_client(db, embedding=seeded_lib, llm=llm)
    try:
        resp = await client.post(
            "/v1/chat", json={"question": "买的保险多久能赔？"}, headers=auth(token)
        )
        events = parse_sse(resp.text)
        names = [n for n, _ in events]

        assert names.count("plan") == 3  # 恰好 3 轮
        assert names.count("tool_call") == 3
        done = events[-1][1]
        assert done["rounds"] == 3
        assert done["convergence_reason"] in ("natural", "max_steps")  # 强制收敛且原因可审计
        assert len(llm.chat_calls) == 5  # 第 3 轮反思走硬规则，不再调 LLM
    finally:
        await client.aclose()
