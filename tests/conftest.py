"""共享 fixture：fake 应用工厂、令牌、SSE 解析、真实 PG 测试库（章程 VII）。"""

import json

import pytest

from src.config import get_settings
from src.data.models import Base
from src.security.jwt import create_token

TENANT = "tenant-test"
OTHER_TENANT = "tenant-other"


def make_app(session_factory, *, rerank=None, llm=None, embedding=None, checkpointer=None):
    """构建注入 fake 的应用（不触碰真实外部服务）。

    use_rerank 固定为 True：测试结果不随 .env 开关漂移（章程 VII 环境隔离）。
    checkpointer 缺省用 MemorySaver；检查点恢复用例传测试 PG saver。
    """
    from langgraph.checkpoint.memory import MemorySaver

    from src.api.main import create_app
    from tests.unit.fakes import FakeEmbedding, FakeLLM, FakeRerank

    settings = get_settings().model_copy()
    settings.use_rerank = True
    return create_app(
        settings,
        embedding=embedding or FakeEmbedding(),
        rerank=rerank or FakeRerank(),
        llm=llm or FakeLLM(),
        session_factory=session_factory,
        checkpointer=checkpointer or MemorySaver(),
    )


@pytest.fixture
def token():
    settings = get_settings()
    return create_token(TENANT, ["retrieval:read"], settings.jwt_secret, settings.jwt_exp_hours)


@pytest.fixture
def other_token():
    settings = get_settings()
    return create_token(
        OTHER_TENANT, ["retrieval:read"], settings.jwt_secret, settings.jwt_exp_hours
    )


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """解析 SSE 文本为 (event, payload) 序列。"""
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ").strip()
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        if event and data is not None:
            events.append((event, data))
    return events


@pytest.fixture
async def db():
    """真实 PG 会话工厂；不可达时跳过（环境未就绪 ≠ 用例失败）。"""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from src.data.db import build_sessionmaker

    url = get_settings().test_database_url
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"测试 PG 不可达（{url}）：{exc}")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = build_sessionmaker(url)

    async def cleanup():
        async with engine.begin() as conn:
            for table in ("runtime_log", "message", "session", "chunk", "document"):
                await conn.execute(text(f"DELETE FROM {table}"))

    await cleanup()
    yield factory
    await cleanup()
    await engine.dispose()


@pytest.fixture
async def seeded_lib(db):
    """播种：等待期条款 + 干扰条款（保单贷款），返回与索引一致的 FakeEmbedding。"""
    from src.data import dao
    from src.data.models import Document
    from src.rag.chunker import split_sections
    from src.rag.indexer import index_document
    from src.rag.parser import parse_document
    from tests.unit.fakes import FakeEmbedding

    waiting = (
        "康护一生重大疾病保险条款\n"
        "2.3.1 等待期\n"
        "自本合同生效（或最后复效）之日起 90 日内为等待期。\n"
        "被保险人在等待期内发生保险事故的，本公司不承担给付保险金的责任。\n"
    )
    distraction = (
        "鑫享一生年金保险条款\n"
        "6.2 保单贷款\n"
        "在本合同有效期内，投保人可以凭本合同向本公司申请保单贷款，"
        "贷款金额不超过本合同当时现金价值净值的 80%。\n"
    )

    embedding = FakeEmbedding()
    async with db() as session:
        for title, body in (
            (waiting.splitlines()[0], waiting),
            (distraction.splitlines()[0], distraction),
        ):
            doc = await dao.add_document(
                session,
                Document(
                    tenant_id=TENANT,
                    title=title,
                    source="corpus_import",
                    content_hash=f"seed-{title}",
                    version=1,
                    status="processing",
                    raw_text=body,
                ),
            )
            sections = parse_document(body)
            parents, children = split_sections(sections)
            await index_document(session, embedding, TENANT, doc.id, parents, children)
        await session.commit()
    return embedding


def build_client(session_factory, **kwargs):
    """带 app_state 句柄的 ASGI 测试客户端（供用例定制 rerank/llm/checkpointer）。"""
    import httpx

    app = make_app(session_factory, **kwargs)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    client.app_state = app.state
    return client


@pytest.fixture
async def seed_waiting_clause(seeded_lib, db):
    """播种 + 默认 fake 客户端。"""
    client = build_client(db, embedding=seeded_lib)
    yield client
    await client.aclose()
