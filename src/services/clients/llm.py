"""LLM 客户端契约（章程 VI）：流式增量输出（research D6/D11）。"""

from collections.abc import AsyncIterator
from typing import Protocol

import httpx


class LlmClient(Protocol):
    def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        """按序产出回答增量 delta；调用方聚合为完整答案。"""
        ...


class DashscopeLlm:
    def __init__(
        self, base_url: str, api_key: str, model: str, max_tokens: int = 400, timeout_s: float = 60.0
    ):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_s)
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._max_tokens = max_tokens

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "max_tokens": self._max_tokens,
        }
        async with self._client.stream(
            "POST", "/chat/completions", headers=self._headers, json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                body = line.removeprefix("data: ").strip()
                if body == "[DONE]":
                    break
                delta = body and _delta_text(body)
                if delta:
                    yield delta


def _delta_text(body: str) -> str | None:
    import json

    try:
        chunk = json.loads(body)
    except json.JSONDecodeError:
        return None
    choices = chunk.get("choices") or []
    if not choices:
        return None
    return choices[0].get("delta", {}).get("content") or None
