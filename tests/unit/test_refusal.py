"""T033：拒答判定——阈值边界（等于/低于）、零命中、建议文案。"""

from src.rag.rerank import rerank_hits
from src.services.answer import REFUSAL_TEXT, should_refuse
from src.services.clients.rerank import RerankClient


class FixedRerank(RerankClient):
    async def rerank(self, query, docs):
        return [0.1, 0.9, 0.5, 0.3, 0.7, 0.2, 0.4][: len(docs)]


def test_threshold_boundary_equal_is_not_refused():
    hits = [{"score": 0.35, "parent_text": "等待期 90 日"}]
    assert should_refuse(hits, 0.35) is False  # 等于阈值 → 不拒答


def test_threshold_below_is_refused():
    hits = [{"score": 0.34, "parent_text": "无关内容"}]
    assert should_refuse(hits, 0.35) is True


def test_zero_hits_is_refused():
    assert should_refuse([], 0.35) is True


def test_refusal_text_has_suggestion():
    assert "等待期" in REFUSAL_TEXT and "不作推测" in REFUSAL_TEXT


async def test_rerank_hits_orders_and_trims():
    hits = [{"parent_text": f"doc{i}"} for i in range(6)]
    ranked = await rerank_hits(FixedRerank(), "q", hits, top_k=3)
    assert [h["score"] for h in ranked] == [0.9, 0.7, 0.5]  # 降序 + 截断 top_k


async def test_rerank_hits_empty_passthrough():
    assert await rerank_hits(FixedRerank(), "q", [], top_k=5) == []
