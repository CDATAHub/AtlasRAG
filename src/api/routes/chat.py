"""POST /v1/chat — 问答（SSE 流式，contracts/002 api.md §1）。"""

import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.api.schemas import ChatRequest, error_body
from src.data import dao
from src.security.jwt import TenantContext, parse_token
from src.services.answer import chat_stream

router = APIRouter()


def get_ctx(request: Request) -> TenantContext:
    """租户上下文只来自 JWT claims（章程 V）。"""
    settings = request.app.state.settings
    try:
        return parse_token(
            (request.headers.get("authorization") or "").removeprefix("Bearer ").strip(),
            settings.jwt_secret,
        )
    except Exception:  # noqa: BLE001 —— 令牌缺失/无效统一 401（FR-012）
        raise HTTPException(401, detail=error_body("unauthorized", "缺少或无效的访问令牌")) from None


def encode_sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _resolve_session(request: Request, ctx: TenantContext, raw: str | None):
    """会话解析：不存在/已删/跨租户 → 404（FR-017，不泄露存在性）。"""
    if raw is None:
        return None
    async with request.app.state.session_factory() as session:
        row = await dao.get_session(session, ctx.tenant_id, uuid.UUID(raw))
    if row is None:
        raise HTTPException(404, detail=error_body("session_not_found", "会话不存在"))
    return row.id


@router.post("/v1/chat")
async def chat(payload: ChatRequest, request: Request):
    ctx = get_ctx(request)
    question = payload.question.strip()
    if not question:
        raise HTTPException(422, detail=error_body("invalid_request", "问题不能为空"))

    settings = request.app.state.settings
    session_factory = request.app.state.session_factory

    async with session_factory() as session:  # 空库预检 → 503（FR-010）
        if await dao.count_children(session, ctx.tenant_id) == 0:
            return JSONResponse(
                status_code=503,
                content=error_body("service_unavailable", "条款库暂不可用，请稍后再试"),
            )

    session_id = await _resolve_session(request, ctx, payload.session_id)

    async def gen(first: tuple[str, dict], agen: AsyncIterator) -> AsyncIterator[str]:
        yield encode_sse(*first)
        async for event in agen:
            yield encode_sse(*event)

    async with session_factory() as session:
        agen = chat_stream(
            session,
            session_factory,
            ctx_tenant_id=ctx.tenant_id,
            question=question,
            session_id=session_id,
            client_msg_id=payload.client_msg_id,
            graph=request.app.state.graph,
            settings=settings,
        )
        try:
            first = await agen.__anext__()
        except StopAsyncIteration:  # pragma: no cover —— chat_stream 必产出事件
            return JSONResponse(
                status_code=503, content=error_body("service_unavailable", "无响应内容")
            )
        return StreamingResponse(
            gen(first, agen),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
