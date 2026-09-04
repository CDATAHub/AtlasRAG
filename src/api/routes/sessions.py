"""会话管理接口（US4 / contracts/002 api.md §2/§3）：历史查询与软删。"""

import uuid

from fastapi import APIRouter, HTTPException, Request, Response

from src.api.routes.chat import get_ctx
from src.data import dao

router = APIRouter()


async def _get_owned_session(request: Request, tenant_id: str, raw: str):
    try:
        session_id = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(404, detail={"code": "session_not_found", "message": "会话不存在"}) from None
    async with request.app.state.session_factory() as session:
        row = await dao.get_session(session, tenant_id, session_id)
    if row is None:  # 不存在 / 已删 / 跨租户 → 统一 404（FR-017）
        raise HTTPException(404, detail={"code": "session_not_found", "message": "会话不存在"})
    return row


@router.get("/v1/sessions/{session_id}")
async def get_session_history(session_id: str, request: Request):
    ctx = get_ctx(request)
    row = await _get_owned_session(request, ctx.tenant_id, session_id)
    async with request.app.state.session_factory() as session:
        messages = await dao.get_messages(session, ctx.tenant_id, row.id)
    return {
        "session_id": str(row.id),
        "title": row.title,
        "created_at": row.created_at.isoformat(),
        "messages": [
            {
                "message_id": str(m.id),
                "role": m.role,
                "content": m.content,
                **({} if m.role == "user" else {"citations": m.citations or [],
                                                "trace_id": m.trace_id}),
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    }


@router.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    ctx = get_ctx(request)
    row = await _get_owned_session(request, ctx.tenant_id, session_id)
    async with request.app.state.session_factory() as session:
        await dao.soft_delete_session(session, ctx.tenant_id, row.id)
        await session.commit()
    return Response(status_code=204)
