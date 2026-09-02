"""T026：documents 接口契约——去重头、跨租户 404、415、重试 202。"""

import pytest

from tests.conftest import auth

pytestmark = pytest.mark.asyncio

from httpx import ASGITransport, AsyncClient  # noqa: E402

from tests.conftest import make_app  # noqa: E402
from tests.unit.fakes import FakeEmbedding  # noqa: E402

CLAUSE = "2.3.1 等待期\n自本合同生效日起 180 日内为测试等待期。"


async def make_client(db):
    app = make_app(db, embedding=FakeEmbedding())
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    return client, app


async def test_duplicate_upload_returns_dedup_header(db, token):
    client, _ = await make_client(db)
    files = {"file": ("条款.txt", CLAUSE.encode(), "text/plain")}
    first = await client.post("/v1/documents", files=files, headers=auth(token))
    assert first.status_code == 202
    again = await client.post("/v1/documents", files=files, headers=auth(token))
    assert again.status_code == 200
    assert again.headers.get("X-Deduplicated") == "true"
    assert again.json()["doc_id"] == first.json()["doc_id"]
    await client.aclose()


async def test_cross_tenant_status_404(db, token, other_token):
    client, _ = await make_client(db)
    created = await client.post(
        "/v1/documents",
        files={"file": ("条款.txt", CLAUSE.encode(), "text/plain")},
        headers=auth(token),
    )
    doc_id = created.json()["doc_id"]
    foreign = await client.get(f"/v1/documents/{doc_id}/status", headers=auth(other_token))
    assert foreign.status_code == 404  # 不泄露存在性
    await client.aclose()


async def test_pdf_suffix_415(db, token):
    client, _ = await make_client(db)
    resp = await client.post(
        "/v1/documents",
        files={"file": ("条款.pdf", b"%PDF-1.4", "application/pdf")},
        headers=auth(token),
    )
    assert resp.status_code == 415
    assert resp.json()["code"] == "unsupported_media_type"
    await client.aclose()


async def test_reprocess_failed_doc_202(db, token):
    import uuid

    from src.data import dao
    from src.data.models import Document

    client, app = await make_client(db)
    async with app.state.session_factory() as session:
        doc = await dao.add_document(
            session,
            Document(
                tenant_id="tenant-test",
                title="坏文档.txt",
                content_hash="bad-hash",
                version=1,
                status="failed",
                error="ValueError: 空文档",
                raw_text="",
            ),
        )
        await session.commit()
        doc_id = str(doc.id)

    resp = await client.post(f"/v1/documents/{doc_id}/reprocess", headers=auth(token))
    assert resp.status_code == 202
    assert resp.json()["status"] == "processing"

    bogus = await client.post(f"/v1/documents/{uuid.uuid4()}/reprocess", headers=auth(token))
    assert bogus.status_code == 404
    await client.aclose()
