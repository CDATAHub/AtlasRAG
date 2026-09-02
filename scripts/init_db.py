#!/usr/bin/env python3
"""开发库建表（Base.metadata.create_all）。用法：python scripts/init_db.py"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from src.config import get_settings  # noqa: E402
from src.data.models import Base  # noqa: E402


async def main() -> None:
    engine = create_async_engine(get_settings().database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print("开发库表结构就绪")


if __name__ == "__main__":
    asyncio.run(main())
