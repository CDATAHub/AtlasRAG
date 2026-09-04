"""generate 节点（docs/03 §3.4.4；research D3）：证据整合生成带引用草稿。

- 证据池跨步骤去重合并、全局编号；拒答判定沿用阶段 1 口径（阈值/零命中）
- delta 经 stream writer 外发为 answer 事件（FR-015）
- 生成故障不中断：降级为「资料不足」收敛（章程 IV）
"""

from langgraph.config import get_stream_writer

from src.agent.prompts import (
    REFUSAL_TEXT,
    SYSTEM_DIRECT,
    SYSTEM_GENERATOR,
    clip_on_sentence,
    should_refuse,
)
from src.config import Settings
from src.services.citations import build_citations


def make_generate_node(llm, settings: Settings):
    async def generate(state, config):  # noqa: ARG001
        writer = get_stream_writer()
        question = state["question"]
        hits = _flatten_evidence(state.get("evidence") or [])
        threshold = settings.refusal_threshold if settings.use_rerank else 0.0
        ranked = _as_ranked(hits)

        # 直答路径（US1 场景 4）：plan 判定常识/无需检索 → 无证据门槛，LLM 直答
        if state.get("route") == "answer" and not (state.get("evidence") or []):
            parts: list[str] = []
            try:
                async for delta in llm.stream_chat(
                    [
                        {"role": "system", "content": SYSTEM_DIRECT},
                        {"role": "user", "content": question},
                    ]
                ):
                    parts.append(delta)
                    writer({"type": "answer", "delta": delta})
            except Exception:  # noqa: BLE001 —— 直答故障也必须收敛（章程 IV）
                parts = ["回答生成暂时失败，请稍后重试。"]
                writer({"type": "answer", "delta": parts[0]})
                return {
                    "draft": parts[0],
                    "refused": True,
                    "citations": [],
                    "hit_count": 0,
                    "top_score": None,
                    "convergence_reason": "generate_failed",
                }
            draft = "".join(parts)
            return {
                "draft": draft,
                "refused": False,
                "direct_answer": True,  # reflect 据此跳过反思（US2）
                "citations": [],  # 直答无条款依据，不带引用（区别于条款问答 FR-006）
                "hit_count": 0,
                "top_score": None,
            }

        if should_refuse(ranked, threshold):  # FR-008：拒答路径（继承阶段 1 口径）
            writer({"type": "answer", "delta": REFUSAL_TEXT})
            top = float(ranked[0]["score"]) if ranked else None
            return {
                "draft": REFUSAL_TEXT,
                "refused": True,
                "citations": [],
                "hit_count": len(hits),
                "top_score": top,
                "convergence_reason": "refused",
            }

        evidence_text = "\n\n".join(
            f"[{n}] {hit['title']} {hit.get('sec_no') or ''}\n{clip_on_sentence(hit['parent_text'], 1600)}"
            for n, hit in hits[:3]
        )
        messages = [
            {"role": "system", "content": SYSTEM_GENERATOR},
            {"role": "user", "content": f"问题：{question}\n\n资料：\n{evidence_text}"},
        ]
        parts: list[str] = []
        try:
            async for delta in llm.stream_chat(messages):
                parts.append(delta)
                writer({"type": "answer", "delta": delta})
        except Exception:  # noqa: BLE001 —— 生成故障也必须收敛（章程 IV）
            text = "回答生成暂时失败，请稍后重试。"
            writer({"type": "answer", "delta": text})
            return {
                "draft": text,
                "refused": True,
                "citations": [],
                "hit_count": len(hits),
                "top_score": float(ranked[0]["score"]),
                "convergence_reason": "generate_failed",
            }

        draft = "".join(parts)
        citation_items = build_citations(draft, ranked, question)
        return {
            "draft": draft,
            "refused": False,
            "citations": citation_items,
            "hit_count": len(hits),
            "top_score": float(ranked[0]["score"]),
        }

    return generate


def _flatten_evidence(entries: list[dict]) -> list[tuple[int, dict]]:
    """跨步骤证据去重合并 + 全局编号 1..N（同文档同条款取先出现者）。"""
    seen: set[tuple] = set()
    hits: list[tuple[int, dict]] = []
    for entry in entries:
        for hit in entry.get("hits") or []:
            key = (hit["doc_id"], hit.get("sec_no"), hit["parent_text"])
            if key in seen:
                continue
            seen.add(key)
            hits.append((len(hits) + 1, hit))
    return hits[:8]  # 上下文上限：超过后取先出现的高相关项


def _as_ranked(hits: list[tuple[int, dict]]) -> list[dict]:
    """转为 build_citations/should_refuse 期待的 ranked 形态（n/score 降序由工具保证）。"""
    return [
        {**hit, "n": n, "score": float(hit.get("score", 0.0))}
        for n, hit in hits
    ]
