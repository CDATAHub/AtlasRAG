"""契约级 Fake 客户端（章程 VII）：行为与真实契约一致，确定性、可编程。

- FakeEmbedding：字符 bigram 哈希向量（L2 归一化）——相同文本余弦为 1，
  共享字词的文本具有正相似度，可驱动真实检索 SQL 路径。
- FakeRerank：可编程分数（函数 / 固定序列 / 默认字符覆盖率）。
- FakeLLM：按脚本吐 delta / 返回结构化 JSON（plan/reflect），可编程 usage。
"""

import hashlib
import asyncio
from collections.abc import AsyncIterator
from typing import ClassVar

from pydantic import BaseModel, Field

from src.services.clients.embedding import EmbeddingClient
from src.services.clients.llm import LlmClient, LlmResult
from src.services.clients.rerank import RerankClient
from src.tools.base import ToolPolicy, ToolTimeoutError, ToolTransientError
from src.tools.hybrid_search import HybridSearchResult


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
        stream_scripts: list[list[str]] | None = None,
    ):
        self.deltas = deltas if deltas is not None else ["等待期为 90 日", "，自合同生效日起算[1]。"]
        self.chat_responses = list(chat_responses) if chat_responses is not None else None
        self.stream_scripts = [list(s) for s in stream_scripts] if stream_scripts else None
        self.usage_tokens = usage_tokens
        self.calls: list[list[dict]] = []  # stream_chat 收到的 messages
        self.chat_calls: list[list[dict]] = []
        self.response_formats: list[dict | None] = []
        self._usage_seq = usage_tokens if isinstance(usage_tokens, list) else None
        self._usage_fixed = usage_tokens if isinstance(usage_tokens, int) else 0

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        self.calls.append(messages)
        if self.stream_scripts:
            deltas = self.stream_scripts.pop(0) if self.stream_scripts else self.deltas
        else:
            deltas = self.deltas
        for delta in deltas:  # noqa: ASYNC110 —— 脚本化假客户端，无需真实流
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


class FakeToolArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200)


class FakeTool:
    """可编程故障工具（specs/003 research D9）：驱动 executor 逐中间件断言。

    - hang_s：invoke 挂起秒数（超时路径）
    - fail_first_n：前 n 次抛 ToolTransientError（重试路径）
    - exc：每次都抛的异常（unknown/permanent 路径）
    - policy：覆写执行策略（非幂等/不可并行标注）
    """

    name: ClassVar[str] = "fake"
    description: ClassVar[str] = "可编程故障测试工具"
    scope: ClassVar[str] = "retrieval:read"
    args_model: ClassVar[type[BaseModel]] = FakeToolArgs
    result_model: ClassVar[type[BaseModel]] = HybridSearchResult
    policy: ToolPolicy = ToolPolicy(timeout_ms=200, max_retries=1)  # 类级缺省，子类可覆写

    def __init__(
        self,
        *,
        hang_s: float = 0.0,
        fail_first_n: int = 0,
        exc: Exception | None = None,
        policy: ToolPolicy | None = None,
        result: HybridSearchResult | None = None,
    ):
        self.hang_s = hang_s
        self.fail_first_n = fail_first_n
        self.exc = exc
        if policy is not None:  # 仅显式传入时实例覆写；否则保留子类类级声明
            self.policy = policy
        self.result = result or HybridSearchResult(hits=[], top_score=None)
        self.invoke_count = 0

    async def invoke(self, ctx, args):  # noqa: ARG002 —— ctx 由 executor 注入
        self.invoke_count += 1
        if self.fail_first_n and self.invoke_count <= self.fail_first_n:
            raise ToolTransientError(f"transient #{self.invoke_count}")
        if self.hang_s:
            await asyncio.sleep(self.hang_s)
        if self.exc is not None:
            raise self.exc
        return self.result


__all__ = [
    "FakeEmbedding",
    "FakeRerank",
    "FakeLLM",
    "FakeTool",
    "FakeToolArgs",
    "ToolTimeoutError",
    "ToolTransientError",
]
