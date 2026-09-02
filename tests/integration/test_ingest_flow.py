"""T027：上传→自动入库→可被检索（US2 集成主路径）。"""

import asyncio

import pytest

from tests.conftest import auth

pytestmark = pytest.mark.asyncio

CLAUSE = """测试专用重大疾病保险条款
2.3.1 等待期
自本合同生效日起 180 日内为本合同特有的测试等待期。
6.2 保单贷款
贷款金额不超过现金价值净值的 70%。"""


async def test_upload_ingest_and_retrievable(db, token):
    from httpx import ASGITransport, AsyncClient

    from tests.conftest import make_app
    from tests.unit.fakes import FakeEmbedding

    app = make_app(db, embedding=FakeEmbedding())
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    files = {"file": ("测试专用重疾险条款.txt", CLAUSE.encode(), "text/plain")}
    resp = await client.post("/v1/documents", files=files, headers=auth(token))
    assert resp.status_code == 202
    doc_id = resp.json()["doc_id"]

    # 轮询至 indexed（SC-005：入库窗口内可检索）
    status = {}
    for _ in range(100):
        status = (await client.get(f"/v1/documents/{doc_id}/status", headers=auth(token))).json()
        if status["status"] == "indexed":
            break
        await asyncio.sleep(0.05)
    assert status["status"] == "indexed"
    assert status["blocks"]["parents"] >= 1 and status["blocks"]["children"] >= 1

    # 入库后可被检索命中（对独有内容提问，FR-004/005）
    chat = await client.post(
        "/v1/chat", json={"question": "测试等待期是多少天？"}, headers=auth(token)
    )
    assert chat.status_code == 200
    assert "180 日" in chat.text or "180日" in chat.text.replace(" ", "")
    await client.aclose()
