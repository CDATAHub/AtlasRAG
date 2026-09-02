#!/usr/bin/env python3
"""拼接 docs/ 为根目录单文件合并版（生成物，勿手改）。

用法：python3 scripts/build_combined_doc.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = ROOT / "AtlasRAG-架构设计文档.md"


def main() -> None:
    chapters = sorted(DOCS.glob("[0-9][0-9]-*.md"))
    parts = [(DOCS / "README.md").read_text(encoding="utf-8").rstrip()]
    for ch in chapters:
        parts.append(ch.read_text(encoding="utf-8").strip())
    OUT.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    print(f"built {OUT.relative_to(ROOT)} from README + {len(chapters)} chapters")


if __name__ == "__main__":
    main()
