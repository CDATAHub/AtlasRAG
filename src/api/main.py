"""FastAPI 应用工厂：依赖注入组合根（章程 VI/VII）。

测试传入 fake 客户端与测试 session_factory，不触碰真实外部服务。
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.routes import chat, documents
from src.api.schemas import error_body
from src.config import Settings, get_settings
from src.data.db import build_sessionmaker
from src.services.answer import RetrievalUnavailable
from src.services.clients.embedding import DashscopeEmbedding, EmbeddingClient
from src.services.clients.llm import DashscopeLlm, LlmClient
from src.services.clients.rerank import DashscopeRerank, RerankClient


def create_app(
    settings: Settings | None = None,
    *,
    embedding: EmbeddingClient | None = None,
    rerank: RerankClient | None = None,
    llm: LlmClient | None = None,
    session_factory=None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="AtlasRAG", version="0.1.0")
    # 开发期：prototype 以 file:// 或本地端口打开，跨域访问 API（生产收紧来源）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Deduplicated"],
    )
    app.state.settings = settings
    app.state.embedding = embedding or DashscopeEmbedding(
        settings.llm_base_url,
        settings.llm_api_key,
        settings.embedding_model,
        settings.embedding_dim,
    )
    app.state.rerank = rerank or DashscopeRerank(
        settings.rerank_endpoint, settings.llm_api_key, settings.rerank_model
    )
    app.state.llm = llm or DashscopeLlm(
        settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_max_tokens
    )
    app.state.session_factory = session_factory or build_sessionmaker(settings.database_url)

    app.include_router(chat.router)
    app.include_router(documents.router)

    @app.get("/v1/health")
    async def health():
        return {"status": "ok"}

    @app.exception_handler(RetrievalUnavailable)
    async def retrieval_unavailable_handler(_request: Request, _exc: RetrievalUnavailable):
        return JSONResponse(
            status_code=503,
            content=error_body("service_unavailable", "条款库暂不可用，请稍后再试"),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException):
        """统一错误结构：HTTPException.detail 为 dict 时直接作为响应体（{code,message,trace_id}）。"""
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body("error", str(exc.detail)),
        )

    return app


app = create_app()
