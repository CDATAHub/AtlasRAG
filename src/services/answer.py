"""问答编排（阶段 2）：快路径 → 会话/消息持久化 → AgentLoop 图执行 → SSE 事件流。

产出 (event, payload) 序列由路由层编码为 SSE（contracts/002 api.md §1）。
done 事件由本层基于图终态统一构造，保证任何终止路径（自然收敛/超时/故障）
事件面一致；链路熔断 20s（章程 IV）超时在流内降级，不断连。
"""

import asyncio
import contextlib
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.prompts import (  # noqa: F401 —— REFUSAL_TEXT/should_refuse/clip_on_sentence 兼容阶段 1 导入
    DEGRADED_TEXT,
    REFUSAL_TEXT,
    clip_on_sentence,
    should_refuse,
)
from src.agent.prompts import SYSTEM_SUMMARIZER
from src.config import Settings
from src.data import dao
from src.data.models import Message
from src.services import context_window
from src.services.runtime_log import write_log

logger = logging.getLogger(__name__)


class RetrievalUnavailable(Exception):
    """检索能力不可用（保留阶段 1 异常名，路由层 503 处理仍挂载）。"""

CHITCHAT_PATTERNS = (
    r"^(你好|您好|嗨|哈喽|hi|hello|在吗|在么)[！!。~～\s]*$",
    r"^(谢谢|多谢|感谢|辛苦了|麻烦了|thank you|thanks)[！!。~～\s]*$",
    r"^(你是谁|你叫什么|介绍一下你自己|再见|拜拜|bye)[！!。~～\s]*$",
)

EVENT_NAME = {
    "plan": "plan",
    "tool_call": "tool_call",
    "evidence": "evidence",
    "answer": "answer",
    "citations": "citations",
}


def is_chitchat(question: str, settings: Settings) -> bool:
    """寒暄快路径判定（research D8）：规则表 + 长度上限，零 LLM/检索。"""
    if len(question) > settings.chitchat_max_chars:
        return False
    normalized = question.strip().lower()
    return any(re.match(p, normalized) for p in CHITCHAT_PATTERNS)


async def chat_stream(
    session: AsyncSession,
    session_factory,
    *,
    ctx_tenant_id: str,
    question: str,
    session_id: uuid.UUID,
    client_msg_id: str | None,
    graph,
    llm,
    settings: Settings,
) -> AsyncIterator[tuple[str, dict]]:
    trace_id = f"tr-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()

    await dao.set_session_status(session, ctx_tenant_id, session_id, "running")
    message_id = uuid.uuid4()
    client_msg_id = client_msg_id or f"srv-{uuid.uuid4().hex[:12]}"
    if await dao.find_message(session, ctx_tenant_id, session_id, client_msg_id) is None:
        # 中断重跑（US5）时提问行已存在：跳过插入，避免违反幂等唯一约束
        await dao.append_message(
            session,
            Message(
                session_id=session_id,
                tenant_id=ctx_tenant_id,
                client_msg_id=client_msg_id,
                role="user",
                content=question,
            ),
        )
    await session.commit()

    if is_chitchat(question, settings):  # 寒暄：模板直答，零 LLM/检索（SC-005）
        async for event in _chitchat_events(session, session_factory, ctx_tenant_id, question,
                                            trace_id, session_id, message_id, client_msg_id, started):
            yield event
        return

    # 多轮背景（FR-011/014）：滑窗保留近期对话，超阈值压缩旧对话（证据链保留）
    history_rows = await dao.get_messages(session, ctx_tenant_id, session_id)
    if history_rows and history_rows[-1].role == "user":
        history_rows = history_rows[:-1]  # 排除当前提问
    history = [
        {"role": m.role, "content": m.content, "citations": m.citations or []}
        for m in history_rows
    ]
    history_text, summary_tokens = await context_window.build_context(
        history, settings, _summarizer(llm)
    )

    graph_input = {
        "question": question,
        "tenant_id": ctx_tenant_id,
        "trace_id": trace_id,
        "session_id": str(session_id),
        "message_id": str(message_id),
        "client_msg_id": client_msg_id,
        "history_text": history_text,
        "tokens_used": summary_tokens,
        "messages": [{"role": "user", "content": question}],
    }
    config = {
        "configurable": {
            "thread_id": f"{session_id}:{client_msg_id}",  # 每条消息一次图执行（research D2）
            "db": session,
            "session_factory": session_factory,
        }
    }
    answer_parts: list[str] = []
    final: dict = {}
    refused = False
    reason = "natural"

    # 熔断实现：图执行放在独立生产者任务，消费端按截止时间取事件（research D9）。
    # 不用 asyncio.timeout 包住当前任务——任务级 cancel 会穿透 ASGI 层的 anyio 任务组。
    queue: asyncio.Queue = asyncio.Queue()

    async def _drain() -> None:
        last: dict = {}
        async for mode, chunk in graph.astream(
            graph_input, config, stream_mode=["custom", "values"]
        ):
            if mode == "custom":
                await queue.put((chunk.get("type"), {k: v for k, v in chunk.items() if k != "type"}))
            else:
                last = chunk  # values：每节点后的最新状态，最后一次为终态
        await queue.put(("__final__", last))

    task = asyncio.create_task(_drain())
    deadline = time.monotonic() + settings.chain_timeout_s
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            try:
                ev_type, payload = await asyncio.wait_for(queue.get(), remaining)
            except TimeoutError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
                raise
            if ev_type == "__final__":
                final = payload
                break
            if ev_type == "answer":
                answer_parts.append(payload.get("delta") or "")
            name = EVENT_NAME.get(ev_type)
            if name:
                yield name, payload
    except TimeoutError:
        refused = True
        reason = "timeout"
        if not answer_parts:
            yield "answer", {"delta": DEGRADED_TEXT}
        await _rollback(session)
    except Exception:  # noqa: BLE001 —— 图内未预期故障必须收敛（章程 IV）
        logger.exception("AgentLoop 执行异常")
        refused = True
        reason = "generate_failed"
        if not answer_parts:
            yield "answer", {"delta": "回答生成暂时失败，请稍后重试。"}
        await _rollback(session)
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    done = _build_done(
        trace_id, started, session_id, message_id, client_msg_id, final, refused, reason
    )
    yield "done", done
    await _persist(
        session, session_factory, ctx_tenant_id, question, trace_id, session_id, message_id,
        client_msg_id, done, answer_parts, final, refused,
    )


async def _chitchat_events(
    session, session_factory, tenant_id, question, trace_id, session_id, message_id,
    client_msg_id, started,
):
    text = "您好！我是保险条款问答助手，请描述您想了解的条款问题，例如「等待期是多久」。"
    done = {
        "trace_id": trace_id,
        "session_id": str(session_id),
        "message_id": str(message_id),
        "client_msg_id": client_msg_id,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "refused": False,
        "hit_count": 0,
        "top_score": None,
        "convergence_reason": "natural",
        "rounds": 0,
        "steps": 0,
        "tokens_used": 0,
    }
    yield "answer", {"delta": text}
    yield "citations", {"citations": []}
    yield "done", done
    await dao.append_message(
        session,
        Message(
            session_id=session_id,
            tenant_id=tenant_id,
            client_msg_id=client_msg_id,
            role="assistant",
            content=text,
            citations=[],
            trace_id=trace_id,
        ),
    )
    await dao.set_session_status(session, tenant_id, session_id, "idle")
    await session.commit()
    await write_log(
        session_factory,
        trace_id=trace_id,
        tenant_id=tenant_id,
        question=question,
        hit_count=0,
        top_score=None,
        latency_ms=done["latency_ms"],
        refused=False,
        answer=text,
        session_id=session_id,
        message_id=message_id,
        client_msg_id=client_msg_id,
        plan_rounds=0,
        steps=0,
        tokens_used=0,
        convergence_reason="natural",
    )


async def _rollback(session) -> None:
    """被取消的图执行可能打断进行中的 DB 操作，先复位会话再持久化。"""
    try:
        await session.rollback()
    except Exception:  # noqa: BLE001
        logger.debug("取消后会话复位失败", exc_info=True)


def _summarizer(llm):
    """旧对话压缩摘要器（FR-014）：一次非流式 LLM 调用。"""

    async def summarize(text: str) -> str:
        result = await llm.chat(
            [
                {"role": "system", "content": SYSTEM_SUMMARIZER},
                {"role": "user", "content": text},
            ]
        )
        return result.content

    return summarize


def _build_done(
    trace_id: str,
    started: float,
    session_id: uuid.UUID,
    message_id: uuid.UUID,
    client_msg_id: str,
    final: dict,
    refused: bool,
    reason: str,
) -> dict:
    return {
        "trace_id": trace_id,
        "session_id": str(session_id),
        "message_id": str(message_id),
        "client_msg_id": client_msg_id,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "refused": refused or bool(final.get("refused")),
        "hit_count": int(final.get("hit_count") or 0),
        "top_score": final.get("top_score"),
        "convergence_reason": reason if refused else (final.get("convergence_reason") or reason),
        "rounds": int(final.get("plan_rounds") or 1),
        "steps": int(final.get("steps") or 0),
        "tokens_used": int(final.get("tokens_used") or 0),
    }


async def _persist(
    session, session_factory, tenant_id, question, trace_id, session_id, message_id,
    client_msg_id, done, answer_parts, final, refused,
) -> None:
    answer_text = "".join(answer_parts)
    try:
        await dao.append_message(
            session,
            Message(
                session_id=session_id,
                tenant_id=tenant_id,
                client_msg_id=client_msg_id,
                role="assistant",
                content=answer_text,
                citations=final.get("citations") or [],
                trace_id=trace_id,
            ),
        )
        await dao.set_session_status(session, tenant_id, session_id, "idle")
        await session.commit()
    except (Exception, asyncio.CancelledError):  # noqa: BLE001 —— 持久化失败不影响响应（档案兜底）
        logger.exception("会话消息持久化失败")
    await write_log(
        session_factory,
        trace_id=trace_id,
        tenant_id=tenant_id,
        question=question,
        hit_count=done["hit_count"],
        top_score=done["top_score"],
        latency_ms=done["latency_ms"],
        refused=refused or bool(final.get("refused")),
        answer=answer_text or None,
        session_id=session_id,
        message_id=message_id,
        client_msg_id=client_msg_id,
        plan_rounds=done["rounds"],
        steps=done["steps"],
        tokens_used=done["tokens_used"],
        convergence_reason=done["convergence_reason"],
    )
