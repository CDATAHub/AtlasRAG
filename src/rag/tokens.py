"""分词单源（ADR-008）：jieba + 保险术语词典，写入侧与查询侧共用同一规则。"""

import jieba

_PUNCT = "，。？ ！？、；：:「」『』（）()【】\n\r\t"

for _term in [
    "等待期", "免赔额", "犹豫期", "宽限期", "现金价值", "重疾", "轻症",
    "保险事故", "保险责任", "责任免除", "保单贷款", "给付", "理赔",
]:
    jieba.add_word(_term, freq=1000)

jieba.initialize()


def tokenize(query: str) -> list[str]:
    """查询侧：切词并去掉标点。"""
    return [tok for tok in jieba.lcut(query) if tok.strip() and tok not in _PUNCT]


def segment(text: str) -> str:
    """写入侧：预分词后空格拼接（配合 to_tsvector('simple', …)）。"""
    return " ".join(tokenize(text))
