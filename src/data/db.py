"""数据库引擎 / 会话工厂。连接串来自 Settings；测试注入自己的 session_factory。"""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def build_sessionmaker(database_url: str) -> async_sessionmaker:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)
