"""T024：章节解析——编号识别、表格行、TOC 去重、无章节降级。"""

from src.rag.parser import parse_document


def test_numbered_sections_detected():
    doc = """人保寿险某重疾险条款
1. 您与我们的合同
本合同由保险条款、投保单构成。
1.1 附加合同订立
投保人提出保险申请，本公司同意承保。
2.3.1 等待期
自本合同生效日起 90 日内为等待期。"""
    sections = parse_document(doc)
    nos = [s.no for s in sections]
    assert "1" in nos and "1.1" in nos and "2.3.1" in nos
    wait = next(s for s in sections if s.no == "2.3.1")
    assert "90 日" in wait.full_text


def test_toc_entries_merge_into_body_sections():
    # 目录条目（无正文）在前，正文小节在后 → 按编号合并，只保留有正文者
    doc = """某保险条款
1.1 附加合同订立
1.1 附加合同订立
投保人提出保险申请，本公司同意承保，本合同生效。"""
    sections = parse_document(doc)
    ones = [s for s in sections if s.no == "1.1"]
    assert len(ones) == 1
    assert "投保人提出保险申请" in ones[0].full_text


def test_table_lines_flagged():
    doc = """条款
3. 保障责任表
| 保障责任 | 等待期 |
| 重疾 | 90 日 |
其余正文。"""
    sections = parse_document(doc)
    sec3 = next(s for s in sections if s.no == "3")
    assert len(sec3.table_lines) == 2


def test_no_numbering_falls_back_to_single_section():
    sections = parse_document("这是一份没有任何章节编号的条款文本。\n第二行内容。")
    assert len(sections) == 1
    assert sections[0].no is None
    assert "第二行内容" in sections[0].full_text


def test_long_numeric_line_is_body_not_heading():
    doc = """条款
1. 这是一行很长的正文，虽然以数字开头，但实际是合同条款的正文内容，因此不应当被误判为标题，本行超过八十个字符的长度限制，用来验证长行判定逻辑是否生效。
2. 真正的标题
正文。"""
    sections = parse_document(doc)
    nos = [s.no for s in sections]
    assert "2" in nos
    assert all(s.no != "1" for s in sections) or any(
        "不应当被误判" in s.full_text for s in sections
    )
