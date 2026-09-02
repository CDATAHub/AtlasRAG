"""Embedding 客户端契约（章程 VI）与百炼 OpenAI 兼容实现（research D2/D6）。"""

from typing import Protocol

import httpx

from src.services.clients.base import post_json


class EmbeddingClient(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化，返回与输入等长、维度固定的向量列表。"""
        ...


class DashscopeEmbedding:
    BATCH = 10  # 单请求批量上限，防止超过服务端 batch 限制

    def __init__(self, base_url: str, api_key: str, model: str, dim: int, timeout_s: float = 30.0):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_s)
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH):
            batch = texts[start : start + self.BATCH]
            data = await post_json(
                self._client,
                "/embeddings",
                headers=self._headers,
                payload={"model": self._model, "input": batch},
            )
            items = sorted(data["data"], key=lambda item: item["index"])
            vectors.extend(item["embedding"] for item in items)
        return vectors
