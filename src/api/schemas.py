"""请求/响应模型（contracts/api.md）。"""

import uuid

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    session_id: str | None = None  # 可选，缺省新建会话（FR-009）
    client_msg_id: str | None = None  # 幂等键，缺省服务端生成（FR-013）


def error_body(code: str, message: str) -> dict:
    """统一错误结构 {code, message, trace_id}（docs/08 §8.3）。"""
    return {"code": code, "message": message, "trace_id": uuid.uuid4().hex[:12]}
