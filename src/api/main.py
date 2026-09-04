"""FastAPI 应用工厂：依赖注入组合根（章程 VI/VII）。

测试传入 fake 客户端、测试 session_factory 与 checkpointer，不触碰真实外部服务。
生产在 lifespan 创建 AsyncPostgresSaver（research D2）；测试注入 MemorySaver 或
指向测试库的 saver，经 httpx ASGITransport 时不触发 lifespan，图已急构建。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.agent.graph import build_graph, build_tool_registry
from src.api.routes import chat, documents, sessions
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
    checkpointer=None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        saver_cm = None
        if app.state.checkpointer is None:  # 生产：Postgres 检查点（research D2）
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            conn = settings.database_url.replace("+asyncpg", "")
            saver_cm = AsyncPostgresSaver.from_conn_string(conn)
            app.state.checkpointer = await saver_cm.__aenter__()
            await app.state.checkpointer.setup()
        app.state.graph = _build(app)
        async with app.state.session_factory() as s:  # 中断复位（FR-018 / US5）
            from src.services.sessions import reset_interrupted

            await reset_interrupted(s)
        yield
        if saver_cm is not None:
            await saver_cm.__aexit__(None, None, None)

    app = FastAPI(title="AtlasRAG", version="0.2.0", lifespan=lifespan)
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
    app.state.checkpointer = checkpointer
    if checkpointer is not None:  # 测试/注入路径：急构建，无需 lifespan
        app.state.graph = _build(app)

    app.include_router(chat.router)
    app.include_router(documents.router)
    app.include_router(sessions.router)

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


def _build(app: FastAPI):
    """构建 AgentLoop 图（app 级依赖闭包注入，per-run 依赖走 config）。"""
    return build_graph(
        llm=app.state.llm,
        registry=build_tool_registry(app.state.embedding, app.state.rerank, app.state.settings),
        settings=app.state.settings,
        checkpointer=app.state.checkpointer,
    )


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.api.main:app", host="127.0.0.1", port=8000)
