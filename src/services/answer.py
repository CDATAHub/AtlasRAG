"""问答编排（阶段 2）：快路径 → 会话/消息持久化 → AgentLoop 图执行 → SSE 事件流。

产出 (event, payload) 序列由路由层编码为 SSE（contracts/002 api.md §1）。
done 事件由本层基于图终态统一构造，保证任何终止路径（自然收敛/超时/故障）
事件面一致；链路熔断 20s（章程 IV）超时在流内降级，不断连。
"""

import asyncio
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
from src.config import Settings
from src.data import dao
from src.data.models import Message
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
    session_id: uuid.UUID | None,
    client_msg_id: str | None,
    graph,
    settings: Settings,
) -> AsyncIterator[tuple[str, dict]]:
    trace_id = f"tr-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()

    if session_id is None:
        row = await dao.create_session(session, ctx_tenant_id, title=question[:50])
        session_id = row.id
        await dao.set_session_status(session, ctx_tenant_id, session_id, "running")
    else:
        await dao.set_session_status(session, ctx_tenant_id, session_id, "running")
    message_id = uuid.uuid4()
    client_msg_id = client_msg_id or f"srv-{uuid.uuid4().hex[:12]}"
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

    graph_input = {
        "question": question,
        "tenant_id": ctx_tenant_id,
        "trace_id": trace_id,
        "session_id": str(session_id),
        "message_id": str(message_id),
        "client_msg_id": client_msg_id,
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

    try:
        async with asyncio.timeout(settings.chain_timeout_s):
            async for mode, chunk in graph.astream(
                graph_input, config, stream_mode=["custom", "values"]
            ):
                if mode == "custom":
                    ev_type = chunk.get("type")
                    payload = {k: v for k, v in chunk.items() if k != "type"}
                    if ev_type == "answer":
                        answer_parts.append(payload.get("delta") or "")
                    name = EVENT_NAME.get(ev_type or "", None)
                    if name:
                        yield name, payload
                else:
                    final = chunk  # values 模式：每节点后的最新状态，最后一次为终态
    except TimeoutError:
        refused = True
        reason = "timeout"
        if not answer_parts:
            yield "answer", {"delta": DEGRADED_TEXT}
    except Exception:  # noqa: BLE001 —— 图内未预期故障必须收敛（章程 IV）
        logger.exception("AgentLoop 执行异常")
        refused = True
        reason = "generate_failed"
        if not answer_parts:
            yield "answer", {"delta": "回答生成暂时失败，请稍后重试。"}

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
    except Exception:  # noqa: BLE001 —— 会话持久化失败不影响响应（档案兜底）
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
