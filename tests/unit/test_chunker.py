"""T025：父子切分不变式（data-model：子块 50~1200 字符、重叠、父块覆盖）。"""

from src.rag.chunker import split_sections
from src.rag.parser import Section


def _long_body(n: int = 12) -> str:
    return "".join(f"这是第{i}句话，用来测试切分逻辑的稳定性与重叠行为。" for i in range(n))


def test_child_length_invariants():
    sections = [Section(no="1", title="保险责任", body=[_long_body(30)])]
    parents, children = split_sections(sections)
    assert parents and children
    for child in children:
        assert 50 <= len(child.text) <= 1200, f"子块长度越界: {len(child.text)}"


def test_children_link_to_parent_key():
    sections = [Section(no="2.3", title="等待期", body=[_long_body(10)])]
    parents, children = split_sections(sections)
    assert parents[0].key == "2.3"
    assert all(c.parent_key == "2.3" for c in children)


def test_adjacent_children_overlap():
    sections = [Section(no="1", title="保险责任", body=[_long_body(30)])]
    _, children = split_sections(sections)
    for prev, nxt in zip(children, children[1:], strict=False):
        if nxt.is_table_row or prev.is_table_row:
            continue
        overlap = len(set(prev.text) & set(nxt.text[:60]))
        assert overlap > 0  # 相邻子块存在共享内容（10% 重叠）


def test_table_rows_become_children():
    table = ["| 保障责任 | 等待期 | 保额 |", "| 重疾 | 90 日 | 50 万 |", "| 轻症 | 90 日 | 10 万 |"]
    sections = [Section(no="3", title="保障责任表", body=table)]
    parents, children = split_sections(sections)
    table_children = [c for c in children if c.is_table_row]
    assert len(table_children) >= 2
    assert all(c.parent_key == "3" for c in table_children)


def test_no_heading_section_gets_synthetic_key():
    sections = [Section(no=None, title="全文", body=[_long_body(6)])]
    parents, children = split_sections(sections)
    assert parents[0].key == "sec-0"
    assert children and children[0].sec_no is None
