"""T015：RRF 融合正确性（纯函数）。"""

from src.rag.hybrid import rrf_fusion, tokenize


def _ids(fused):
    return [item_id for item_id, _ in fused]


def test_rrf_single_list_preserves_order():
    assert _ids(rrf_fusion([["a", "b", "c"]])) == ["a", "b", "c"]


def test_rrf_intersection_boosts_rank():
    # a、c 两路都命中 → 排在只命中一路的 b 之前
    fused = _ids(rrf_fusion([["a", "b"], ["c", "a"]]))
    assert fused[0] == "a"
    # a：1/61 + 1/62 ≈ 0.0325；b：1/62 ≈ 0.0161；c：1/61 ≈ 0.0164 → c > b
    assert fused[1] == "c"
    assert fused[2] == "b"


def test_rrf_formula_exact():
    # rank 从 0 起：score = Σ 1/(k+rank+1)；k=60
    # x：第一路 rank0（1/61）+ 第二路 rank0（1/61）= 0.0328 > y：第一路 rank1（1/62）
    fused = rrf_fusion([["x", "y"], ["x"]], k=60)
    assert _ids(fused)[0] == "x"
    assert _ids(fused)[1] == "y"
    assert abs(fused[0][1] - (1 / 61 + 1 / 61)) < 1e-9


def test_rrf_disjoint_lists_concatenates_by_score():
    a, b = ["a1", "a2", "a3"], ["b1", "b2"]
    fused = _ids(rrf_fusion([a, b]))
    # 每路首位得分相同 1/61 → 先出现者优先（a1 在前）
    assert fused[:2] == ["a1", "b1"]
    assert set(fused) == set(a) | set(b)


def test_rrf_empty():
    assert rrf_fusion([]) == []
    assert rrf_fusion([[]]) == []


def test_tokenize_drops_punct():
    tokens = tokenize("这款重疾险等待期多久？")
    assert "等待期" in tokens
    assert "？" not in tokens and "。" not in tokens
