#!/usr/bin/env python3
"""导入 kaihe 完整条款语料（102 份）为 document + 异步入库。

用法：python scripts/import_corpus.py [--tenant tenant-001] [--limit N]
数据源：data/raw/kaihe_clauses.jsonl（input 字段为条款全文，首行为产品名）。
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import get_settings  # noqa: E402
from src.data import dao  # noqa: E402
from src.data.db import build_sessionmaker  # noqa: E402
from src.data.models import Document  # noqa: E402
from src.rag.pipeline import run_ingestion  # noqa: E402
from src.services.clients.embedding import DashscopeEmbedding  # noqa: E402


async def main(tenant: str, limit: int | None) -> None:
    settings = get_settings()
    session_factory = build_sessionmaker(settings.database_url)
    embedding = DashscopeEmbedding(
        settings.llm_base_url,
        settings.llm_api_key,
        settings.embedding_model,
        settings.embedding_dim,
    )

    corpus = Path("data/raw/kaihe_clauses.jsonl")
    count = 0
    for line in corpus.read_text(encoding="utf-8").splitlines():
        if limit and count >= limit:
            break
        record = json.loads(line)
        text = (record.get("input") or "").strip()
        if not text:
            continue
        title = text.splitlines()[0].strip()[:120]
        content_hash = __import__("hashlib").sha256(text.encode()).hexdigest()

        async with session_factory() as session:
            if await dao.find_by_hash(session, tenant, content_hash):
                continue
            version = await dao.next_version(session, tenant, title)
            doc = await dao.add_document(
                session,
                Document(
                    tenant_id=tenant,
                    title=title,
                    source="corpus_import",
                    content_hash=content_hash,
                    version=version,
                    status="processing",
                    raw_text=text,
                ),
            )
            await session.commit()
            doc_id = doc.id
        await run_ingestion(session_factory, embedding, doc_id, tenant)
        count += 1
        if count % 10 == 0:
            print(f"已入库 {count} 份…")
    print(f"完成：共入库 {count} 份条款")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", default="tenant-001")
    parser.add_argument("--limit", type=int, default=None)
    asyncio.run(main(parser.parse_args().tenant, parser.parse_args().limit))
