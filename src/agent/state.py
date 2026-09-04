"""AgentState（docs/03 §3.3）：图节点间的共享状态，也是检查点的持久化单元。

- `messages` / `tool_results` / `evidence` 为追加型 reducer：增量合并不重写，
  天然适配多轮与检查点续跑（research D1）。
- 三个证据字段的边界：tool_results ⊇ evidence ⊇ citations（CONTEXT.md）。
"""

import operator
from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    # —— 输入（首次调用注入） ——
    question: str
    tenant_id: str
    trace_id: str
    session_id: str
    message_id: str
    client_msg_id: str

    # —— 消息与多轮 ——
    messages: Annotated[list, add_messages]  # 对话历史（增量合并）

    # —— 任务规划 ——
    plan: list[dict]  # [{"step","action","tool","query","rationale"}]
    current_step: int  # 重规划保留，只替换未执行步骤（FR-005）
    route: Literal["retrieve", "answer"]

    # —— 检索/工具证据 ——
    tool_results: Annotated[list[dict], operator.add]  # 工具原始返回（append-only）
    evidence: Annotated[list[dict], operator.add]  # 进入生成上下文的证据池
    hit_count: int
    top_score: float | None

    # —— 生成与收敛 ——
    draft: str
    refused: bool
    direct_answer: bool  # plan 判定常识直答（generate 设置，reflect 跳过反思）
    reflect_result: dict  # {"sufficient","reason","next_action","next_query"}
    final_answer: str
    citations: list[dict]
    convergence_reason: str  # natural|max_steps|timeout|budget|refused

    # —— 控制与治理 ——
    steps: int
    plan_rounds: int
    tokens_used: int
