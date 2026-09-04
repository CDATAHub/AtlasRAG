"""上下文组装（US4 / research D7；docs/03 §3.6）：滑动窗口 + 阈值摘要压缩。

- 滑动窗口：始终保留最近 N 轮完整消息
- 压缩：历史总长超阈值时，窗口外旧对话压缩为摘要（一次 LLM 调用）
- 证据链不压缩：摘要强制携带已引用的条款出处（citations 摘录），忠实度优先
- token 为近似估算（字符数 ÷ 1.5），仅用于触发判断；预算记账走 usage 回执
"""

from collections.abc import Awaitable, Callable

from src.config import Settings


def approx_tokens(text: str) -> int:
    """中文近似的 token 估算：约 1.5 字符/token。"""
    return max(1, (len(text) * 2 + 2) // 3)


async def build_context(
    history: list[dict],
    settings: Settings,
    summarizer: Callable[[str], Awaitable[str]] | None = None,
) -> tuple[str, int]:
    """组装多轮背景文本。history: [{role, content, citations}]（旧 → 新）。

    返回 (context_text, 该次压缩消耗的 tokens)。
    """
    keep = settings.sliding_window_rounds * 2
    window = history[-keep:]
    older = history[:-keep] if len(history) > keep else []
    total = sum(approx_tokens(m.get("content") or "") for m in history)

    parts: list[str] = []
    used = 0
    if older:
        older_text = _render(older)
        if total > settings.compress_threshold_tokens and summarizer is not None:
            summary = await summarizer(older_text)
            used += approx_tokens(summary)
            parts.append(f"（更早对话摘要，含已引用条款出处）\n{summary}")
        else:
            parts.append(older_text)
    if window:
        parts.append(_render(window))
    return "\n".join(parts), used


def _render(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if m.get("role") == "assistant":
            refs = "；".join(
                f"[{c.get('n')}] {c.get('title')}"
                f"{(' ' + c.get('sec_no')) if c.get('sec_no') else ''}"
                for c in (m.get("citations") or [])[:5]
            )
            suffix = f"（已引用条款：{refs}）" if refs else ""  # 证据链不压缩（FR-014）
            lines.append(f"助手：{content}{suffix}")
        else:
            lines.append(f"用户：{content}")
    return "\n".join(lines)
