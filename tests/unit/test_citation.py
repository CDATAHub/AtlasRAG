"""T016：引用组装（FR-006/007）——[n] 与 citations 一一对应、quote 为原文子句。"""

from src.services.citations import build_citations, extract_refs, pick_quote

HITS = [
    {
        "n": 1, "doc_id": "d-1", "title": "康护一生条款", "sec_no": "2.3.1",
        "parent_text": "2.3.1 等待期\n自本合同生效（或最后复效）之日起 90 日内为等待期。"
                       "被保险人在等待期内发生保险事故的，本公司不承担给付责任。",
        "score": 0.92,
    },
    {
        "n": 2, "doc_id": "d-1", "title": "康护一生条款", "sec_no": "2.3.2",
        "parent_text": "2.3.2 等待期除外\n投保时已如实告知既往病史的，等待期内事故仍承担责任。",
        "score": 0.87,
    },
]


def test_extract_refs_ordered_dedup():
    assert extract_refs("等待期为 90 日[1]，责任终止[2]，见[1]再[3]") == [1, 2, 3]


def test_extract_refs_empty():
    assert extract_refs("没有引用标记") == []


def test_build_citations_maps_answer_refs():
    answer = "等待期为 90 日[1]；等待期内事故不赔[2]。"
    citations = build_citations(answer, HITS, "等待期多久")
    assert [c["n"] for c in citations] == [1, 2]
    assert citations[0]["doc_id"] == "d-1"
    assert citations[0]["quote"] in HITS[0]["parent_text"]  # quote 是原文片段（FR-007）


def test_build_citations_falls_back_to_top1():
    answer = "等待期为 90 日。"  # LLM 未标注 → 回退 top-1（FR-006）
    citations = build_citations(answer, HITS, "等待期多久")
    assert [c["n"] for c in citations] == [1]


def test_build_citations_ignores_out_of_range():
    answer = "结论[9]不存在的引用"
    assert build_citations(answer, HITS, "等待期")[0]["n"] == 1


def test_pick_quote_prefers_query_overlap():
    parent = HITS[0]["parent_text"]
    quote = pick_quote(parent, "等待期 90 日")
    assert "90 日" in quote and "等待期" in quote
