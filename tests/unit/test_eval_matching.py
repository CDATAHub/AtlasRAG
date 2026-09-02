"""T037：评测命中判定——quote 级（参考）与事实级（验收口径）。"""

from src.eval.matching import extract_facts, is_fact_hit, is_quote_hit, normalize_ws


def test_quote_hit_with_whitespace_noise():
    quote = "之日起 180 日内因意外伤害"
    parent = "自本合同生效（或最后复效）\n之日起 180 日内因意外伤害以外的原因确诊重大疾病。"
    assert is_quote_hit(quote, [parent]) is True


def test_quote_miss():
    assert is_quote_hit("等待期为 90 日", ["宽限期为 60 日"]) is False


def test_extract_facts_numbers_and_units():
    facts = extract_facts("等待期为 90 日，赔付比例 30%，保额 10万元")
    assert "90日" in facts and "30%" in facts and "10万元" in facts


def test_fact_hit_across_product_equivalent_clause():
    """事实级（口径 A）：其他产品的同类型条款，关键事实一致即命中。"""
    gold = "诉讼时效期间为 2 年，自其知道或者应当知道保险事故发生之日起计算。"
    other_product = "本合同的诉讼时效期间为2年，自其知道或应当知道保险事故发生之日起计算。"
    assert is_fact_hit(gold, [other_product]) is True


def test_fact_miss_when_fact_absent():
    assert is_fact_hit("等待期为 90 日", ["宽限期为 60 日"]) is False


def test_fact_hit_none_when_no_facts():
    assert is_fact_hit("本合同构成复杂，见条款", ["任意原文"]) is None


def test_normalize_removes_all_whitespace():
    assert normalize_ws(" a b\tc\nd ") == "abcd"
