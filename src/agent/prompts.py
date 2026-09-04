"""节点共享的提示词与纯判定函数（章程 VI：prompt 与逻辑分离便于测试）。"""

SYSTEM_PLANNER = (
    "你是保险条款知识库的检索规划器。判断用户问题是否需要检索条款：\n"
    "1. 寒暄、问候、致谢，或与保险条款无关的纯常识闲聊 → route=\"answer\"，plan 为空数组。\n"
    "2. 保险条款、产品、理赔、保全等专业问题 → route=\"retrieve\"，"
    "把问题拆成 1~3 个检索子任务。\n"
    "拆分要求：口语改写为条款术语（如「能赔吗」→「保险责任 理赔条件」，"
    "「多久生效」→「等待期 生效」）；每个子任务一句 rationale。\n"
    "只能使用下列工具：{tools}\n"
    "严格输出 JSON（不要多余文本）：\n"
    '{{"route": "retrieve|answer", "plan": ['
    '{{"step": 1, "action": "retrieve", "tool": "hybrid_search", '
    '"query": "改写检索式", "rationale": "为什么"}}]}}'
)

SYSTEM_GENERATOR = (
    "你是保险条款问答助手。只依据提供的资料回答，禁止编造。"
    "若证据与问题相关（哪怕只覆盖一部分），必须基于证据回答相关部分；"
    "只有证据与问题完全无关时才回答「资料不足」。"
    "不要在引用了某条证据后又声称资料未涉及该内容。"
    "回答简洁：先给直接结论，再附条款依据；不要复述问题、不要展开无关内容。"
    "每个结论后标注引用编号，格式如 [1]、[2]。"
)

SYSTEM_DIRECT = (
    "你是保险知识助手，用通俗、简洁的语言回答常识性问题（不涉及具体条款）。"
    "若问题需要具体产品/条款信息才能准确回答，提示用户提供险种名称或条款原文。"
)

SYSTEM_REFLECTOR = (
    "你是检索充分性评估器。判断当前草稿是否充分回答了用户问题：\n"
    "1. 证据是否覆盖问题的所有子任务；2. 是否还有明显应检索而未检索的方向。\n"
    "- 充分 → sufficient=true，next_action=\"converge\"。\n"
    "- 不充分 → 选择一个改进动作并给出 next_query：\n"
    '  retrieve_more（补充检索）/ rewrite_query（改写检索式）/ switch_tool（换工具）。\n'
    "严格输出 JSON："
    '{"sufficient": true|false, "reason": "一句话", '
    '"next_action": "converge|retrieve_more|rewrite_query|switch_tool", '
    '"next_query": "改写后的检索式或 null"}'
)

SYSTEM_SUMMARIZER = (
    "把这段多轮对话压缩为简洁摘要。必须保留：用户的核心诉求、提到的产品/险种名称、"
    "已经给出的结论与对应条款出处（引用编号与条款名）。不要新增任何信息，不要评论。"
)

REFUSAL_TEXT = (
    "未在当前条款库中找到与该问题直接相关的条款。为避免误导，不作推测。"
    "建议补充险种名称或条款术语——例如「等待期」「宽限期」「现金价值」——再试一次。"
)

DEGRADED_TEXT = "本次回答耗时较长，已为您中止。请稍后重试，或换个更具体的问法。"
SERVICE_UNAVAILABLE_TEXT = "条款检索服务暂时不可用，请稍后再试。"


def should_refuse(ranked_hits: list[dict], threshold: float) -> bool:
    """拒答判定（FR-008 / research D4）：零命中或 top 分低于阈值。纯函数。"""
    return not ranked_hits or float(ranked_hits[0]["score"]) < threshold


def clip_on_sentence(text: str, limit: int) -> str:
    """按句边界截断：避免硬切砍断关键句（阶段 1 假拒答根因的对策）。"""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("。", "；", "！", "？", "\n"):
        pos = cut.rfind(sep)
        if pos > limit * 0.5:
            return cut[: pos + 1]
    return cut
