"""重排客户端契约（章程 VI）与百炼原生实现（research D4/D6）。

rerank 无 OpenAI 兼容端点（compatible-mode 下 404），走 DashScope 原生服务：
POST {endpoint}  payload: {"model": …, "input": {"query", "documents"},
"parameters": {"top_n"}} → 响应 output.results[index, relevance_score]。
端点与字段差异只落在实现类，RerankClient 契约不受影响。
"""

from typing import Protocol

import httpx

from src.services.clients.base import post_json


class RerankClient(Protocol):
    async def rerank(self, query: str, docs: list[str]) -> list[float]:
        """返回与 docs 等长的相关性分数（越高越相关）。"""
        ...


class DashscopeRerank:
    def __init__(self, endpoint: str, api_key: str, model: str, timeout_s: float = 30.0):
        self._client = httpx.AsyncClient(timeout=timeout_s)
        self._endpoint = endpoint
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model

    async def rerank(self, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        data = await post_json(
            self._client,
            self._endpoint,
            headers=self._headers,
            payload={
                "model": self._model,
                "input": {"query": query, "documents": docs},
                "parameters": {"return_documents": False, "top_n": len(docs)},
            },
        )
        scores = [0.0] * len(docs)
        for item in data["output"]["results"]:
            scores[int(item["index"])] = float(item["relevance_score"])
        return scores
