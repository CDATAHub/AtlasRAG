"""POST /v1/chat — 问答（SSE 流式，contracts/api.md §3）。"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.api.schemas import ChatRequest, error_body
from src.data import dao
from src.security.jwt import TenantContext, parse_token
from src.services.answer import RetrievalUnavailable, answer_stream

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

    async def gen(first: tuple[str, dict], agen: AsyncIterator) -> AsyncIterator[str]:
        yield encode_sse(*first)
        async for event in agen:
            yield encode_sse(*event)

    async with session_factory() as session:
        agen = answer_stream(
            session,
            session_factory,
            ctx_tenant_id=ctx.tenant_id,
            question=question,
            embedding=request.app.state.embedding,
            reranker=request.app.state.rerank,
            llm=request.app.state.llm,
            hybrid_top_k=settings.hybrid_top_k,
            rerank_top_k=settings.rerank_top_k,
            use_rerank=settings.use_rerank,
            refusal_threshold=settings.refusal_threshold,
            chain_timeout_s=settings.chain_timeout_s,
        )
        try:
            # 检索/重排发生在首个 yield 之前：此处的 RetrievalUnavailable 可转 503
            first = await agen.__anext__()
        except RetrievalUnavailable:
            return JSONResponse(
                status_code=503,
                content=error_body("service_unavailable", "条款库暂不可用，请稍后再试"),
            )
        except StopAsyncIteration:  # pragma: no cover —— answer_stream 必产出 done
            return JSONResponse(
                status_code=503, content=error_body("service_unavailable", "无响应内容")
            )
        return StreamingResponse(
            gen(first, agen),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
