"""POST /v1/chat — 问答（SSE 流式，contracts/002 api.md §1）。

会话生命周期闸门（US4）：解析/创建 → 串行化（409）→ 幂等重放 → 持锁流式执行。
"""

import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.api.schemas import ChatRequest, error_body
from src.data import dao
from src.security.jwt import TenantContext, parse_token
from src.services import sessions
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


def _sse_response(agen: AsyncIterator) -> StreamingResponse:
    async def gen():
        async for event in agen:
            yield encode_sse(*event)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/v1/chat")
async def chat(payload: ChatRequest, request: Request):
    ctx = get_ctx(request)
    question = payload.question.strip()
    if not question:
        raise HTTPException(422, detail=error_body("invalid_request", "问题不能为空"))

    settings = request.app.state.settings
    session_factory = request.app.state.session_factory

    async with session_factory() as s:  # 空库预检 → 503（FR-010）
        if await dao.count_children(s, ctx.tenant_id) == 0:
            return JSONResponse(
                status_code=503,
                content=error_body("service_unavailable", "条款库暂不可用，请稍后再试"),
            )

    # —— 会话解析 / 创建（FR-009） ——
    if payload.session_id:
        session_id = await _resolve_session(request, ctx, payload.session_id)
        lock = sessions.lock_for(session_id)
        if lock.locked():  # 串行化（FR-012）
            return JSONResponse(
                status_code=409,
                content=error_body("session_busy", "上一条回答仍在进行中，请稍后再试"),
            )
    else:
        async with session_factory() as s:
            row = await dao.create_session(s, ctx.tenant_id, title=question[:50])
            await s.commit()
        session_id = row.id
        lock = sessions.lock_for(session_id)

    # —— 幂等重放（FR-013）：同键已完成请求重放既有事件流，不重复生成 ——
    if payload.client_msg_id:
        async with session_factory() as s:
            assistant_row = await sessions.find_replayable(
                s, ctx.tenant_id, session_id, payload.client_msg_id
            )
            if assistant_row and assistant_row.trace_id:
                log = await dao.get_log_by_trace(s, ctx.tenant_id, assistant_row.trace_id)
            else:
                log = None
        if assistant_row:
            async def replay_stream():
                for event in sessions.replay_events(assistant_row, log):
                    yield encode_sse(*event)

            return StreamingResponse(
                replay_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    # —— 持锁执行（锁的生命周期 = 响应流生命周期） ——
    async def locked_stream():
        async with lock:
            async with session_factory() as session:
                agen = chat_stream(
                    session,
                    session_factory,
                    ctx_tenant_id=ctx.tenant_id,
                    question=question,
                    session_id=session_id,
                    client_msg_id=payload.client_msg_id,
                    graph=request.app.state.graph,
                    llm=request.app.state.llm,
                    settings=settings,
                )
                async for event in agen:
                    yield event

    return _sse_response(locked_stream())
