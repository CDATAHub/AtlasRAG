"""三重收敛保险（章程 IV / research D9）：步数、预算、超时的确定性判定。

- 步数上限：route/reflect 硬规则（见 nodes/route.py、nodes/reflect.py）
- token 预算：usage 回执累加（plan/reflect），耗尽后拒发下一次 LLM 调用
- 熔断超时：编排层 asyncio.timeout（见 services/answer.py）
本模块只放纯判定函数，供节点与测试共用。
"""

from src.config import Settings

BUDGET_DEGRADED_TEXT = "本次问答的资源预算已耗尽，已停止进一步检索。请稍后重试或简化问题。"


def budget_exhausted(state: dict, settings: Settings) -> bool:
    """token 预算是否耗尽（>= 视为耗尽：不再发起任何新 LLM 调用）。"""
    return state.get("tokens_used", 0) >= settings.token_budget


def budget_stop(state: dict) -> dict:
    """预算耗尽的 generate 降级输出：明确提示、不编造（FR-007/008）。"""
    return {
        "draft": BUDGET_DEGRADED_TEXT,
        "refused": True,
        "citations": [],
        "hit_count": len(
            [h for e in state.get("evidence") or [] for h in e.get("hits") or []]
        ),
        "top_score": None,
        "convergence_reason": "budget",
    }
