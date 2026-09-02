"""父子切分（research D5）：父块 = 完整小节；子块 300~500 token（约 450~750 字）、
10% 重叠；表格行单独成子块。父块独立成行，子块经 parent 关联（FR-006/007、data-model）。"""

import re
from dataclasses import dataclass

from src.rag.parser import Section

_SENT_SPLIT = re.compile(r"(?<=[。；！？!?])")
TARGET_CHARS = 600  # ≈400 token
OVERLAP_CHARS = 60  # ≈10%
MIN_CHARS, MAX_CHARS = 50, 1200  # data-model 子块长度不变式


@dataclass
class ParentBlock:
    key: str
    sec_no: str | None
    text: str


@dataclass
class ChildBlock:
    parent_key: str
    sec_no: str | None
    text: str
    position: int
    is_table_row: bool = False


def _split_sentences(body: str) -> list[str]:
    parts = [p for p in _SENT_SPLIT.split(body) if p.strip()]
    return parts or ([body] if body.strip() else [])


def _chunk_prose(body: str) -> list[str]:
    """按句累积至 TARGET_CHARS；相邻块带 OVERLAP_CHARS 尾部重叠。"""
    sentences = _split_sentences(body)
    chunks: list[str] = []
    buf = ""
    for sent in sentences:
        if buf and len(buf) + len(sent) > TARGET_CHARS:
            chunks.append(buf.strip())
            buf = buf[-OVERLAP_CHARS:] + sent
        else:
            buf += sent
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


def split_sections(sections: list[Section]) -> tuple[list[ParentBlock], list[ChildBlock]]:
    parents: list[ParentBlock] = []
    children: list[ChildBlock] = []
    for idx, sec in enumerate(sections):
        key = sec.no or f"sec-{idx}"
        parents.append(ParentBlock(key=key, sec_no=sec.no, text=sec.full_text))
        position = 0
        for piece in _chunk_prose(sec.full_text):
            if len(piece) >= MIN_CHARS or (len(piece) >= 20 and not sec.table_lines):
                children.append(
                    ChildBlock(parent_key=key, sec_no=sec.no, text=piece, position=position)
                )
                position += 1
        for line in sec.table_lines:
            if len(line) >= 10:
                children.append(
                    ChildBlock(
                        parent_key=key, sec_no=sec.no, text=line, position=position, is_table_row=True
                    )
                )
                position += 1
    return parents, children
