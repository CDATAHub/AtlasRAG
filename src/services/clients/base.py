"""外部服务调用公共层（章程 III）：超时 + 仅可重试错误指数退避。

可重试：网络抖动、超时、5xx、限流（429 / 403+Throttling）；
4xx 参数/权限错误直接失败，不浪费重试。
"""

import asyncio

import httpx

RETRIABLE_STATUS = range(500, 600)
THROTTLE_STATUS = (429, 403)


def _is_throttled(resp: httpx.Response) -> bool:
    """百炼限流的响应形态：429，或 403 + Throttling 类错误码。"""
    if resp.status_code == 429:
        return True
    if resp.status_code == 403:
        try:
            code = str(resp.json().get("code", ""))
        except Exception:  # noqa: BLE001
            return False
        return "throttl" in code.lower() or "rate limit" in code.lower()
    return False


async def post_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict,
    payload: dict,
    retries: int = 2,
) -> dict:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in RETRIABLE_STATUS or _is_throttled(resp):
                resp.raise_for_status()
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in RETRIABLE_STATUS and not _is_throttled(
                exc.response
            ):
                raise  # 其他 4xx：不可重试（章程 III）
            last_exc = exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
        if attempt < retries:
            await asyncio.sleep(2.0 * (attempt + 1))  # 限流退避需要比网络抖动更长
    raise last_exc  # type: ignore[misc]
