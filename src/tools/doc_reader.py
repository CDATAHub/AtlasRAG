"""doc_reader 工具契约（章程 I；clarify Q1）：本阶段仅定义契约，不注册实现。

阶段 3 随 Tool Registry 落地；契约先行保证 plan prompt 与工具面的措辞稳定。
"""

from pydantic import BaseModel, Field

SCOPE = "retrieval:read"


class DocReaderArgs(BaseModel):
    doc_id: str = Field(description="条款文档 ID")
    sec_no: str | None = Field(default=None, description="章节编号（如 2.3.1），缺省读全文大纲")


class DocReaderResult(BaseModel):
    doc_id: str
    title: str
    sections: list[dict]  # [{sec_no, title, text}]


class DocReaderTool:
    """未实现：注册前不可被模型调用（Registry.get 返回 None → 工具面不含它）。"""

    name = "doc_reader"
    description = "按文档 ID / 章节编号读取条款原文"
    scope = SCOPE
    args_model = DocReaderArgs
    result_model = DocReaderResult
