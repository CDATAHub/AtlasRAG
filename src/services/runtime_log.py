"""本地最小运行档案（FR-013 / clarify Q4）：每次问答一行，失败不阻断问答。"""

import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from src.data.models import RuntimeLog

logger = logging.getLogger(__name__)


async def write_log(session_factory: async_sessionmaker, **fields) -> None:
    try:
        async with session_factory() as session:
            session.add(RuntimeLog(**fields))
            await session.commit()
    except Exception:  # noqa: BLE001 —— 观测失败不影响主链路
        logger.exception("runtime_log 写入失败")
