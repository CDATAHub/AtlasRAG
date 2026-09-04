"""T032：上下文压缩（US4）——滑窗、阈值触发、证据链保留（FR-014）。"""

from src.config import get_settings
from src.services.context_window import approx_tokens, build_context

SETTINGS = get_settings()


def _msg(role: str, content: str, citations=None) -> dict:
    return {"role": role, "content": content, "citations": citations or []}


def test_approx_tokens_chinese_ratio():
    assert approx_tokens("") == 1
    assert approx_tokens("一" * 150) == 100  # 1.5 字符/token


async def test_window_keeps_recent_turns():
    history = [_msg("user", f"问题{i}") for i in range(20)]
    text, used = await build_context(history, SETTINGS)
    assert used == 0  # 未超阈值，无压缩
    assert "问题19" in text  # 最近轮保留
    assert "问题0" in text  # 未超阈值：旧对话原样保留


async def test_compression_over_threshold_with_summarizer():
    """超过 3000 token → 旧对话压缩为摘要，滑窗内原样保留。"""
    history = [_msg("user", "长内容" * 300) for _ in range(30)]  # 远超阈值
    calls: list[str] = []

    async def summarizer(text: str) -> str:
        calls.append(text)
        return "【摘要】用户此前询问了等待期问题。"

    text, used = await build_context(history, SETTINGS, summarizer)
    assert calls and "【摘要】" in text  # 旧对话已压缩
    assert used > 0  # 摘要消耗记账
    assert text.count("用户：") == SETTINGS.sliding_window_rounds * 2  # 滑窗内原样保留（12 条）


async def test_evidence_chain_survives_compression():
    """FR-014：摘要必须携带已引用条款出处（证据链不压缩）。"""
    history = [
        _msg("user", "等待期多久？" * 100),
        _msg("assistant", "等待期 90 日" * 100,
             citations=[{"n": 1, "title": "康护一生条款", "sec_no": "2.3.1"}]),
    ] * 20
    async def summarizer(text: str) -> str:
        return "摘要占位"

    text, _ = await build_context(history, SETTINGS, summarizer)
    assert "康护一生条款" in text and "2.3.1" in text  # citations 摘录随摘要保留
