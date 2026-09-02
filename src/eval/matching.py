"""评测命中判定（US4 / research D9）。

双口径：
- **事实级（验收口径，用户选定 A）**：gold_answer 中的关键事实（天数/比例/金额等）
  出现在 top-5 任一父块原文中 → 命中。识别「其他产品的同类型条款」，贴近真实体验。
- **quote 级（参考口径）**：标准原文片段子串包含，作为参考指标一并输出。
无关键事实的题目回退 quote 判定。
"""

import re

_WS = re.compile(r"\s+")
_FACT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|日|天|年|个月|月|万元|千元|元|倍|次|周岁)")


def normalize_ws(text: str) -> str:
    return _WS.sub("", text or "")


def extract_facts(gold_answer: str) -> list[str]:
    return [normalize_ws(m.group(0)) for m in _FACT.finditer(gold_answer or "")]


def is_quote_hit(quote: str, parent_texts: list[str]) -> bool:
    needle = normalize_ws(quote)
    if not needle:
        return False
    return any(needle in normalize_ws(t) for t in parent_texts)


def is_fact_hit(gold_answer: str, parent_texts: list[str]) -> bool | None:
    """事实级判定（多数语义）：过半关键事实出现在父块原文 → 命中。

    gold 无关键事实（如 L3 计算题的推导值）时返回 None（调用方回退 quote 判定）。
    """
    facts = extract_facts(gold_answer)
    if not facts:
        return None
    joined = normalize_ws("".join(parent_texts))
    present = sum(1 for f in facts if f in joined)
    return present * 2 >= len(facts)
