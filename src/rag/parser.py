"""轻量结构解析（research D5）：纯文本条款 → 章节小节。

kaihe 语料实测「1 / 1.1 / 2.3.1」数字编号完整（data/README 质检），
按行识别标题；无编号文本降级为单节。表格行（| 分隔）保留在节内并打标。

条款正文前通常有「条款目录」：目录条目会命中标题正则但无正文，
与正文小节同编号——按编号合并去重（保留有正文者），避免父子映射撞 key。
"""

import re
from dataclasses import dataclass, field

# 「1.」「1.1」「2.3.1 标题」；行首允许空白
_HEADING = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\.?\s+(\S.{0,60})$")
_TABLE_SEP = re.compile(r"^\s*\|")


@dataclass
class Section:
    no: str | None  # 章节编号，如 "2.3.1"；无编号时为 None
    title: str
    body: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        head = f"{self.no} {self.title}" if self.no else self.title
        return "\n".join([head, *self.body])

    @property
    def table_lines(self) -> list[str]:
        return [line for line in self.body if _TABLE_SEP.match(line)]


def parse_document(text: str) -> list[Section]:
    entries: list[Section] = []
    current: Section | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        match = _HEADING.match(line)
        if match and len(line.strip()) <= 80:  # 长行是正文恰好以数字开头的情形
            current = Section(no=match.group(1), title=match.group(2).strip())
            entries.append(current)
            continue
        if current is None:
            current = Section(no=None, title=line.strip()[:60] or "全文")
            entries.append(current)
            continue
        current.body.append(line.strip())
    if not entries:
        entries.append(Section(no=None, title="全文", body=[]))

    # 按编号合并：目录条目（空正文）让位于正文小节；无编号节只保留首个
    merged: dict[str, Section] = {}
    order: list[str] = []
    pseudo: Section | None = None
    for sec in entries:
        if sec.no is None:
            if pseudo is None:
                pseudo = sec
            else:
                pseudo.body.extend(sec.body)
            continue
        if sec.no not in merged:
            merged[sec.no] = sec
            order.append(sec.no)
            continue
        existing = merged[sec.no]
        if len("\n".join(sec.body)) > len("\n".join(existing.body)):
            sec.body = sec.body or existing.body
            merged[sec.no] = sec

    result = ([pseudo] if pseudo else []) + [merged[no] for no in order]
    return result
