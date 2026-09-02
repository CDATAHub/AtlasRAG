"""文档上传 / 状态 / 重试（contracts/api.md §1/§2，US2）。"""

import asyncio
import hashlib
import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from src.api.routes.chat import get_ctx
from src.api.schemas import error_body
from src.data import dao
from src.data.models import Document
from src.rag.pipeline import run_ingestion

router = APIRouter()

SUPPORTED_SUFFIX = (".txt", ".md")
_MAX_BYTES = 5 * 1024 * 1024  # 5MB，单份条款足够


@router.post("/v1/documents")
async def upload(request: Request, file: UploadFile = File(...)):
    ctx = get_ctx(request)
    filename = file.filename or "未命名.txt"
    if not filename.lower().endswith(SUPPORTED_SUFFIX):
        return JSONResponse(
            status_code=415,
            content=error_body(
                "unsupported_media_type",
                "本阶段仅支持纯文本条款（.txt/.md）；PDF 解析为后续子项",
            ),
        )
    raw = await file.read()
    if len(raw) > _MAX_BYTES:
        raise HTTPException(413, detail=error_body("payload_too_large", "文档超过 5MB 上限"))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            422, detail=error_body("invalid_request", "文件不是有效的 UTF-8 文本")
        ) from None
    if not text.strip():
        raise HTTPException(422, detail=error_body("invalid_request", "文档内容为空"))

    content_hash = hashlib.sha256(text.encode()).hexdigest()
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        existing = await dao.find_by_hash(session, ctx.tenant_id, content_hash)
        if existing is not None:  # 同版本归并（FR-003 / clarify Q2）
            return JSONResponse(
                status_code=200,
                headers={"X-Deduplicated": "true"},
                content={
                    "doc_id": str(existing.id),
                    "title": existing.title,
                    "version": existing.version,
                    "status": existing.status,
                },
            )
        version = await dao.next_version(session, ctx.tenant_id, filename)
        doc = await dao.add_document(
            session,
            Document(
                tenant_id=ctx.tenant_id,
                title=filename,
                content_hash=content_hash,
                version=version,
                status="processing",
                raw_text=text,
            ),
        )
        await session.commit()
        doc_id = doc.id

    # 后台入库（research D10）：进程内异步任务，失败转 failed 可重试
    asyncio.create_task(run_ingestion(session_factory, request.app.state.embedding, doc_id, ctx.tenant_id))

    return JSONResponse(
        status_code=202,
        content={"doc_id": str(doc_id), "title": filename, "version": version, "status": "processing"},
    )


@router.get("/v1/documents/{doc_id}/status")
async def status(doc_id: str, request: Request):
    ctx = get_ctx(request)
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        doc = await dao.get_document(session, ctx.tenant_id, _uuid_or_404(doc_id))
        if doc is None:  # 他租户视为不存在（不泄露存在性）
            raise HTTPException(404, detail=error_body("not_found", "文档不存在"))
        blocks = await dao.count_blocks(session, ctx.tenant_id, doc.id)
    return {
        "doc_id": str(doc.id),
        "title": doc.title,
        "version": doc.version,
        "status": doc.status,
        "blocks": {"parents": blocks["parent"], "children": blocks["child"]},
        "error": doc.error,
    }


@router.post("/v1/documents/{doc_id}/reprocess")
async def reprocess(doc_id: str, request: Request):
    ctx = get_ctx(request)
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        doc = await dao.get_document(session, ctx.tenant_id, _uuid_or_404(doc_id))
        if doc is None:
            raise HTTPException(404, detail=error_body("not_found", "文档不存在"))
        doc.status = "processing"
        doc.error = None
        await session.commit()
    asyncio.create_task(
        run_ingestion(session_factory, request.app.state.embedding, doc.id, ctx.tenant_id)
    )
    return JSONResponse(status_code=202, content={"doc_id": doc_id, "status": "processing"})


def _uuid_or_404(doc_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(404, detail=error_body("not_found", "文档不存在")) from None
