"""引用组装（FR-006/007）：引用必须来自检索命中块本身，禁止合成。

- 从最终答案文本中提取 [n] 引用标记 → 组装 citations（保持 n 的出现顺序）
- LLM 未按规范标注时回退 top-1（FR-006：非拒答回答必须 ≥1 引用）
- quote 从父块原文按句截取（含查询关键词的句子优先），不改写拼接
"""

import re

CITATION_RE = re.compile(r"\[(\d+)\]")

_SENT_SPLIT = re.compile(r"(?<=[。；！？!?])")


def extract_refs(answer: str) -> list[int]:
    """按出现顺序去重提取 [n]。"""
    seen: list[int] = []
    for match in CITATION_RE.finditer(answer):
        n = int(match.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def pick_quote(parent_text: str, query: str, max_len: int = 120) -> str:
    """从父块原文取一句：与查询字符重叠最多的句子；并列取更靠前者。"""
    sentences = [s.strip() for s in _SENT_SPLIT.split(parent_text) if s.strip()]
    if not sentences:
        return parent_text[:max_len]
    q = set(query) - set("，。？ ！？\n的了吗呢吧")
    best, best_overlap = sentences[0], -1
    for sentence in sentences:
        overlap = len(q & set(sentence))
        if overlap > best_overlap:
            best, best_overlap = sentence, overlap
    return best[:max_len]


def build_citations(
    answer: str,
    ranked_hits: list[dict],
    query: str,
) -> list[dict]:
    """ranked_hits: rerank 后的 Hit（含 n=1.. 序号、doc_id/title/sec_no/parent_text/score）。"""
    refs = [n for n in extract_refs(answer) if 1 <= n <= len(ranked_hits)]
    if not refs:  # 回退 top-1，保证 FR-006
        refs = [1] if ranked_hits else []
    citations = []
    for n in refs:
        hit = ranked_hits[n - 1]
        citations.append(
            {
                "n": n,
                "doc_id": str(hit["doc_id"]),
                "title": hit["title"],
                "sec_no": hit.get("sec_no"),
                "quote": pick_quote(hit["parent_text"], query),
                "score": round(float(hit["score"]), 4),
            }
        )
    return citations
