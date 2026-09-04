"""LLM 客户端契约（章程 VI）：流式生成 + 非流式结构化调用（research D4/D6）。

`stream_chat` 供 generate 节点流式输出；`chat` 供 plan/reflect 结构化输出
（response_format=json_object），并返回 usage 供 token 预算记账（FR-007）。
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

import httpx

from src.services.clients.base import post_json


@dataclass
class LlmResult:
    content: str
    tokens: int  # usage.total_tokens（预算记账口径，spec clarify「计费」）


class LlmClient(Protocol):
    def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        """按序产出回答增量 delta；调用方聚合为完整答案。"""
        ...

    async def chat(
        self, messages: list[dict], *, response_format: dict | None = None
    ) -> LlmResult: ...


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

    async def chat(
        self, messages: list[dict], *, response_format: dict | None = None
    ) -> LlmResult:
        payload: dict = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        data = await post_json(
            self._client, "/chat/completions", headers=self._headers, payload=payload
        )
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        tokens = int((data.get("usage") or {}).get("total_tokens") or 0)
        return LlmResult(content=content, tokens=tokens)


def _delta_text(body: str) -> str | None:
    try:
        chunk = json.loads(body)
    except json.JSONDecodeError:
        return None
    choices = chunk.get("choices") or []
    if not choices:
        return None
    return choices[0].get("delta", {}).get("content") or None
