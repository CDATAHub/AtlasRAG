"""T039：评测脚本核心可复现——播种库上 evaluate_one 产出可断言的命中/拒答。"""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio

from scripts.run_retrieval_eval import evaluate_one  # noqa: E402
from tests.conftest import TENANT  # noqa: E402
from tests.unit.fakes import FakeEmbedding, FakeRerank  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "eval_mini.jsonl"


async def test_eval_mini_dataset(seed_waiting_clause, token):
    """种子库含康护一生（等待期 90 日）与鑫享一生（保单贷款 80%）两条款。"""
    client = seed_waiting_clause
    session_factory = client.app_state.session_factory
    settings = client.app_state.settings
    items = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]

    async with session_factory() as session:
        # MINI-0001：等待期问题 → 命中（quote「90 日内为等待期」在父块原文中）
        hit, refused, score, quote_hit, top5 = await evaluate_one(
            session, FakeEmbedding(), FakeRerank(), TENANT, items[0], settings, settings.refusal_threshold
        )
        assert hit is True
        assert refused is False
        assert top5

        # MINI-0003：L4 库外问题 → 应被拒答（无相关命中）
        hit4, refused4, score4, _, _ = await evaluate_one(
            session, FakeEmbedding(), FakeRerank(), TENANT, items[2], settings, settings.refusal_threshold
        )
        assert hit4 is False
        assert refused4 is True or (score4 is not None and score4 < settings.refusal_threshold)
