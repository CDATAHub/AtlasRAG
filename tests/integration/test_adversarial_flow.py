"""T030：对抗输入集成（US3 / SC-003 mock 化子集）——每条输入确定性停止。"""

import json

import pytest

from tests.conftest import auth, build_client, parse_sse
from tests.unit.fakes import FakeLLM, FakeRerank

pytestmark = pytest.mark.asyncio

REFLECT_INSUFFICIENT = json.dumps(
    {"sufficient": False, "reason": "不足", "next_action": "rewrite_query",
     "next_query": "改写"},
    ensure_ascii=False,
)

VALID_REASONS = {"natural", "max_steps", "timeout", "budget", "refused"}


def _plan(queries: list[str]) -> str:
    return json.dumps(
        {"route": "retrieve",
         "plan": [{"step": i + 1, "action": "retrieve", "tool": "hybrid_search",
                   "query": q, "rationale": "r"} for i, q in enumerate(queries)]},
        ensure_ascii=False,
    )


async def test_adversarial_inputs_all_converge(seeded_lib, db, token):
    """诱导循环/持续无命中/超预算/超长输入：全部在上限内确定性停止。"""
    # ① 诱导循环：反思永远不足 → 3 轮上限强制收敛（FR-006）
    looper = FakeLLM(
        chat_responses=[_plan(["条款 保险 合同"]), REFLECT_INSUFFICIENT] * 5,
        stream_scripts=[["部分回答[1]。"]] * 6,
    )
    client = build_client(db, embedding=seeded_lib, llm=looper)
    try:
        resp = await client.post(
            "/v1/chat", json={"question": "条款 保险"}, headers=auth(token)
        )
        events = parse_sse(resp.text)
        done = events[-1][1]
        assert done["rounds"] <= 3 and done["steps"] <= 6
        assert done["convergence_reason"] in VALID_REASONS
    finally:
        await client.aclose()

    # ② 持续无命中：低分拒答，单轮收敛（FR-008）
    client = build_client(
        db, embedding=seeded_lib, rerank=FakeRerank(scores=[0.05] * 10),
        llm=FakeLLM(chat_responses=[_plan(["无 关 词"])]),
    )
    try:
        resp = await client.post(
            "/v1/chat", json={"question": "量子纠缠条款怎么约定？"}, headers=auth(token)
        )
        events = parse_sse(resp.text)
        done = events[-1][1]
        assert done["refused"] is True
        assert done["convergence_reason"] == "refused"
        assert done["rounds"] == 1
    finally:
        await client.aclose()

    # ③ 超预算：拒发下一次 LLM 调用（口径=调用前判定，plan 重试可溢出一次调用）
    client = build_client(
        db, embedding=seeded_lib, llm=FakeLLM(usage_tokens=8000)
    )
    try:
        resp = await client.post(
            "/v1/chat", json={"question": "条款 保险"}, headers=auth(token)
        )
        events = parse_sse(resp.text)
        done = events[-1][1]
        assert done["convergence_reason"] == "budget"
        assert done["tokens_used"] >= 8000  # 档案记录实际用量（FR-007）
        assert done["refused"] is True
    finally:
        await client.aclose()

    # ④ 超长输入：422 校验拒绝，不耗预算（edge case）
    client = build_client(db, embedding=seeded_lib)
    try:
        resp = await client.post(
            "/v1/chat", json={"question": "等" * 501}, headers=auth(token)
        )
        assert resp.status_code == 422
    finally:
        await client.aclose()
