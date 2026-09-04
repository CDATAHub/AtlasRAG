"""契约级 Fake 客户端（章程 VII）：行为与真实契约一致，确定性、可编程。

- FakeEmbedding：字符 bigram 哈希向量（L2 归一化）——相同文本余弦为 1，
  共享字词的文本具有正相似度，可驱动真实检索 SQL 路径。
- FakeRerank：可编程分数（函数 / 固定序列 / 默认字符覆盖率）。
- FakeLLM：按脚本吐 delta / 返回结构化 JSON（plan/reflect），可编程 usage。
"""

import hashlib
from collections.abc import AsyncIterator

from src.services.clients.embedding import EmbeddingClient
from src.services.clients.llm import LlmClient, LlmResult
from src.services.clients.rerank import RerankClient


class FakeEmbedding(EmbeddingClient):
    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for i in range(len(text) - 1):
            gram = text[i : i + 2]
            digest = int(hashlib.md5(gram.encode()).hexdigest(), 16)
            vec[digest % self.dim] += 1.0
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        return [x / norm for x in vec]


class FakeRerank(RerankClient):
    def __init__(
        self,
        scores: list[float] | None = None,
        fn=None,
    ):
        self.scores = scores
        self.fn = fn
        self.calls: list[tuple[str, int]] = []

    async def rerank(self, query: str, docs: list[str]) -> list[float]:
        self.calls.append((query, len(docs)))
        if self.fn is not None:
            return list(self.fn(query, docs))
        if self.scores is not None:
            return [self.scores[i % len(self.scores)] for i in range(len(docs))]
        # 默认：query 字符在文档中的覆盖率（确定性，且对真实语料方向正确）
        q = set(query) - set("，。？ ！？\n")
        return [sum(1 for ch in q if ch in doc) / max(1, len(q)) for doc in docs]


class FakeLLM(LlmClient):
    """脚本化 LLM：`chat_responses` 依次弹出作为 chat() 返回；usage 可编程。

    chat_responses 为空时返回 "{}"（驱动 plan/reflect 解析失败 → 降级路径）。
    """

    def __init__(
        self,
        deltas: list[str] | None = None,
        chat_responses: list[str] | None = None,
        usage_tokens: int | list[int] = 100,
    ):
        self.deltas = deltas if deltas is not None else ["等待期为 90 日", "，自合同生效日起算[1]。"]
        self.chat_responses = list(chat_responses) if chat_responses is not None else None
        self.usage_tokens = usage_tokens
        self.calls: list[list[dict]] = []  # stream_chat 收到的 messages
        self.chat_calls: list[list[dict]] = []
        self.response_formats: list[dict | None] = []
        self._usage_seq = usage_tokens if isinstance(usage_tokens, list) else None
        self._usage_fixed = usage_tokens if isinstance(usage_tokens, int) else 0

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        self.calls.append(messages)
        for delta in self.deltas:  # noqa: ASYNC110 —— 脚本化假客户端，无需真实流
            yield delta

    async def chat(
        self, messages: list[dict], *, response_format: dict | None = None
    ) -> LlmResult:
        self.chat_calls.append(messages)
        self.response_formats.append(response_format)
        if self.chat_responses:
            content = self.chat_responses.pop(0)
        else:
            content = "{}"
        if self._usage_seq is not None:
            tokens = self._usage_seq.pop(0) if self._usage_seq else 0
        else:
            tokens = self._usage_fixed
        return LlmResult(content=content, tokens=tokens)
