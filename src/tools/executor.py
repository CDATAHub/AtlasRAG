"""执行引擎（specs/003 US1；章程 III / docs/04 §4.3）：工具调用统一管道。

固定阶段（FR-004）：权限 → 参数校验 → 幂等判断 → 执行（超时）→ 重试 → 异常恢复。
健壮性全部在此层，不散落工具实现；失败不抛出，返回结构化 ToolExecutionRecord
（FR-010），由 reflect 决策（002 FR-004 口径延续）。

超时用 asyncio.wait_for（research D2）：executor 运行在 LangGraph 图节点内、
不在 ASGI 任务组中，取消仅传播到当前 await 点——002 的任务组穿透教训不适用。
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from src.config import Settings
from src.tools.base import (
    Registry,
    ToolContext,
    ToolError,
    ToolTransientError,
    resolve_policy,
)

# 可重试错误分类（FR-007：仅瞬时故障）；validation/permission/permanent/unknown 直败
RETRYABLE_CLASSES = {"timeout", "transient"}


@dataclass
class ToolCall:
    """一次待执行的工具调用（tool_node 组装；query 用于展示与 reflect 摘要）。"""

    tool: str
    query: str
    args: dict


@dataclass
class ToolExecutionRecord:
    """一次调用的结构化结果（specs/003 data-model.md；FR-010 携带项）。

    不变式：ok=True ⇒ error/error_class 为空；error_class ∈ RETRYABLE_CLASSES 且
    attempts>1 ⇒ 工具幂等（FR-008 非幂等零重发）。attempts=0 表示管道阶段失败
    （未知工具/权限拒绝），未进入工具实现。result 为内存对象，不序列化进 checkpoint。
    """

    tool: str
    query: str
    ok: bool = False
    step: int | None = None
    hits: list[dict] = field(default_factory=list)
    top_score: float | None = None
    error: str | None = None
    error_class: str | None = None
    attempts: int = 0
    duration_ms: int = 0
    trace_id: str | None = None
    result: Any | None = None

    def to_entry(self) -> dict:
        """tool_results/evidence entry（字段名兼容 002：ok/error/hits/top_score）。"""
        return {
            "tool": self.tool,
            "query": self.query,
            "step": self.step,
            "ok": self.ok,
            "hits": self.hits,
            "top_score": self.top_score,
            "error": self.error,
            "error_class": self.error_class,
            "attempts": self.attempts,
            "duration_ms": self.duration_ms,
            "trace_id": self.trace_id,
        }


def _evidence_hits(tool: Any, result: Any) -> list[dict]:
    """结果 → 证据形态（research D5/D6）：优先 result.hits（Pydantic 模型列表），
    其次工具自适配 hits_of(result)（doc_reader 章节 → evidence hit）。"""
    raw = getattr(result, "hits", None)
    if raw is not None:
        return [h.model_dump() for h in raw]
    hits_of = getattr(tool, "hits_of", None)
    return list(hits_of(result)) if hits_of else []


class ToolExecutor:
    """工具执行引擎：Registry + Settings 构建期注入（章程 VII 可替换）。"""

    def __init__(self, registry: Registry, settings: Settings):
        self._registry = registry
        self._settings = settings

    async def execute(
        self,
        call: ToolCall,
        ctx: ToolContext,
        *,
        step: int | None = None,
        trace_id: str | None = None,
    ) -> ToolExecutionRecord:
        t0 = time.monotonic()

        def _record(ok: bool, **kw) -> ToolExecutionRecord:
            return ToolExecutionRecord(
                tool=call.tool, query=call.query, ok=ok, step=step, trace_id=trace_id,
                duration_ms=int((time.monotonic() - t0) * 1000), **kw,
            )

        tool = self._registry.get(call.tool)
        if tool is None:
            return _record(False, error=f"unknown tool: {call.tool}",
                           error_class="permanent", attempts=0)

        policy = resolve_policy(
            tool,
            default_timeout_ms=self._settings.tool_default_timeout_ms,
            default_max_retries=self._settings.tool_default_max_retries,
        )

        # —— 权限（FR-003 执行前二次校验；模型幻觉出的未授权工具名在此拦截）——
        if not set(policy.required_scopes) <= set(ctx.scopes):
            return _record(False, error=f"missing scopes: {list(policy.required_scopes)}",
                           error_class="permission", attempts=0)

        # —— 参数校验（FR-005：进入实现前拦截，不产生半执行副作用）——
        try:
            args = tool.args_model.model_validate(call.args)
        except ValidationError as exc:
            return _record(False, error=str(exc), error_class="validation", attempts=1)

        # —— 幂等判断（FR-008：非幂等零重发）+ 执行/超时/重试/恢复 ——
        max_retries = policy.max_retries if policy.idempotent else 0
        attempts, last_err, last_class = 0, "", "unknown"
        for attempt in range(1 + max_retries):
            attempts = attempt + 1
            try:
                result = await asyncio.wait_for(
                    tool.invoke(ctx, args), timeout=policy.timeout_ms / 1000
                )
                hits = _evidence_hits(tool, result)
                return _record(
                    True, attempts=attempts, result=result, hits=hits,
                    top_score=getattr(result, "top_score", None),
                )
            except asyncio.TimeoutError:
                last_err = f"{tool.name} timed out after {policy.timeout_ms}ms"
                last_class = "timeout"
            except ToolTransientError as exc:
                last_err, last_class = str(exc), "transient"
            except ValidationError as exc:
                last_err, last_class = str(exc), "validation"
            except ToolError as exc:
                last_err, last_class = str(exc), "permanent"
            except Exception as exc:  # noqa: BLE001 —— 统一恢复（FR-010），不中断循环
                last_err, last_class = f"{tool.name} failed: {exc}", "unknown"
            if last_class not in RETRYABLE_CLASSES:
                break
            if attempt < max_retries:  # 指数退避（research D3：base × 2^n）
                await asyncio.sleep(
                    self._settings.retry_backoff_base_s * (2 ** (attempt - 1))
                )
        return _record(False, error=last_err, error_class=last_class, attempts=attempts)

    async def execute_step(
        self,
        calls: list[ToolCall],
        ctx: ToolContext,
        *,
        step: int | None = None,
        trace_id: str | None = None,
    ) -> list[ToolExecutionRecord]:
        """一步多调用编排（FR-011，clarify Q1）：全 parallel_safe → gather 保序合并；
        含任一非 parallel_safe → 整步串行；单调用失败不连带取消其余（SC-008）。"""
        if len(calls) == 1:
            return [await self.execute(calls[0], ctx, step=step, trace_id=trace_id)]

        def _policy_of(call: ToolCall) -> Any:
            tool = self._registry.get(call.tool)
            return (
                resolve_policy(
                    tool,
                    default_timeout_ms=self._settings.tool_default_timeout_ms,
                    default_max_retries=self._settings.tool_default_max_retries,
                )
                if tool
                else None
            )

        policies = [_policy_of(c) for c in calls]
        if all(p is None or p.parallel_safe for p in policies):
            records = await asyncio.gather(
                *(self.execute(c, ctx, step=step, trace_id=trace_id) for c in calls)
            )
            return list(records)
        return [await self.execute(c, ctx, step=step, trace_id=trace_id) for c in calls]
