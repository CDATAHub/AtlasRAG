"""请求/响应模型（contracts/api.md）。"""

import uuid

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


def error_body(code: str, message: str) -> dict:
    """统一错误结构 {code, message, trace_id}（docs/08 §8.3）。"""
    return {"code": code, "message": message, "trace_id": uuid.uuid4().hex[:12]}
