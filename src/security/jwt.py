"""JWT 鉴权（章程 V / ADR-009）：租户身份只来自令牌 claims，请求体不接收 tenant_id。"""

import datetime
import uuid
from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    scopes: list[str]


def create_token(tenant_id: str, scopes: list[str], secret: str, exp_hours: int) -> str:
    now = datetime.datetime.now(datetime.UTC)
    payload = {
        "tenant_id": tenant_id,
        "scopes": scopes,
        "iat": now,
        "exp": now + datetime.timedelta(hours=exp_hours),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def parse_token(token: str, secret: str) -> TenantContext:
    """无效/过期令牌抛 jwt 异常，由调用方转为 401。"""
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    return TenantContext(tenant_id=str(payload["tenant_id"]), scopes=list(payload.get("scopes", [])))


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"code": "unauthorized", "message": message, "trace_id": uuid.uuid4().hex[:12]},
    )


async def require_tenant(
    authorization: str | None = Header(default=None), secret: str | None = None
) -> TenantContext:
    """FastAPI 依赖：从 Bearer 令牌解出租户上下文。

    secret 由应用工厂经 partial 注入（main.create_app），避免读全局配置。
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthorized("缺少访问令牌，请在 Authorization 头携带 Bearer <JWT>")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return parse_token(token, secret or "")
    except jwt.PyJWTError:
        raise _unauthorized("访问令牌无效或已过期") from None
