"""JWT 契约（章程 V / ADR-009）：签发↔解析往返、过期拒绝、错误密钥拒绝。"""

import datetime

import jwt as pyjwt
import pytest

from src.security.jwt import create_token, parse_token


def test_roundtrip():
    token = create_token("tenant-001", ["retrieval:read"], "s3cret", exp_hours=1)
    ctx = parse_token(token, "s3cret")
    assert ctx.tenant_id == "tenant-001"
    assert ctx.scopes == ["retrieval:read"]


def test_expired_token_rejected():
    now = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)
    token = pyjwt.encode(
        {"tenant_id": "t1", "scopes": [], "exp": now}, "s3cret", algorithm="HS256"
    )
    with pytest.raises(pyjwt.ExpiredSignatureError):
        parse_token(token, "s3cret")


def test_wrong_secret_rejected():
    token = create_token("t1", [], "s3cret", exp_hours=1)
    with pytest.raises(pyjwt.InvalidTokenError):
        parse_token(token, "other-secret")


def test_missing_claims_defaults():
    token = pyjwt.encode({"tenant_id": "t2", "exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5)}, "s3cret", algorithm="HS256")
    ctx = parse_token(token, "s3cret")
    assert ctx.tenant_id == "t2"
    assert ctx.scopes == []
