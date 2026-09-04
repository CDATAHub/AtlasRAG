"""会话服务（US4 / research D6）：串行化闸门、幂等重放、中断复位。

- 串行化：进程内 per-session asyncio.Lock；已被占用 → SessionBusy（路由转 409）
- 幂等（FR-013）：同 (tenant, session, client_msg_id) 的已完成请求重放既有事件流；
  中断（有提问无回答）返回 None，由调用方决定重跑/续跑（US5）
- 中断复位：进程启动时把遗留 running 会话标记为 interrupted（main.py 调用）
"""

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data import dao
from src.data.models import Message, RuntimeLog, Session

_locks: dict[str, asyncio.Lock] = {}


def lock_for(session_id: uuid.UUID) -> asyncio.Lock:
    return _locks.setdefault(str(session_id), asyncio.Lock())


class SessionBusy(Exception):
    """同会话已有进行中请求（FR-012）。"""


async def find_replayable(
    session: AsyncSession, tenant_id: str, session_id: uuid.UUID, client_msg_id: str
) -> Message | None:
    """已完成（有问有答）→ 返回回答行供重放；中断（有问无答）→ None；新请求 → None。"""
    user_row = await dao.find_message(session, tenant_id, session_id, client_msg_id)
    if user_row is None:
        return None
    return await dao.find_assistant_message(session, tenant_id, session_id, client_msg_id)


def replay_events(assistant_row: Message, log: RuntimeLog | None) -> list[tuple[str, dict]]:
    """从已存档的回答与运行档案重建事件流（幂等重放，contracts/002 §1）。"""
    done = {
        "trace_id": assistant_row.trace_id,
        "session_id": str(assistant_row.session_id),
        "message_id": str(assistant_row.id),
        "client_msg_id": assistant_row.client_msg_id,
        "latency_ms": log.latency_ms if log else 0,
        "refused": log.refused if log else False,
        "hit_count": log.hit_count if log else 0,
        "top_score": log.top_score if log else None,
        "convergence_reason": log.convergence_reason if log else "natural",
        "rounds": log.plan_rounds if log else 1,
        "steps": log.steps if log else 0,
        "tokens_used": log.tokens_used if log else 0,
        "replayed": True,
    }
    return [
        ("answer", {"delta": assistant_row.content}),
        ("citations", {"citations": assistant_row.citations or []}),
        ("done", done),
    ]


async def reset_interrupted(session: AsyncSession) -> int:
    """进程启动复位：遗留 running → interrupted（US5 续跑判定依据，FR-018）。"""
    rows = list((await session.scalars(select(Session).where(Session.status == "running"))).all())
    for row in rows:
        row.status = "interrupted"
    await session.commit()
    return len(rows)
