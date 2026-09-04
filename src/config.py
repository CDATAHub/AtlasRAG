"""应用配置：环境变量 / .env 单一来源，禁止散落硬编码（tasks Notes）。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 外部服务（百炼 OpenAI 兼容模式）
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""
    llm_model: str = "qwen3.7-flash"
    embedding_model: str = "qwen3.7-text-embedding"
    rerank_model: str = "qwen3.7-text-rerank"
    # rerank 走百炼原生端点（OpenAI 兼容模式下无 /rerank）
    rerank_endpoint: str = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"

    # 数据库
    database_url: str = "postgresql+asyncpg://root:123456@localhost:5432/atlas"
    test_database_url: str = "postgresql+asyncpg://root:123456@localhost:5432/atlas_test"

    # 检索 / 拒答（阈值经 L4 子集校准后更新，tasks T041）
    embedding_dim: int = 1024
    hybrid_top_k: int = 50
    rerank_top_k: int = 5
    use_rerank: bool = True  # 关闭时用 RRF 原始分排序，拒答仅剩零命中判定
    refusal_threshold: float = 0.35

    # 收敛（章程 IV）：链路熔断线，非延迟承诺（docs 02 NFR）
    chain_timeout_s: float = 20.0
    llm_max_tokens: int = 400  # 生成上限：控时延与成本（答案简洁是 NFR 的一部分）

    # AgentLoop 收敛保险（章程 IV / research D9；docs/03 §3.5 默认值）
    max_steps: int = 6
    plan_rounds_max: int = 3  # 回环上限（含首轮）
    token_budget: int = 8000  # 含推理 token，usage 回执累加

    # 多轮上下文（research D7；docs/03 §3.6）
    sliding_window_rounds: int = 6
    compress_threshold_tokens: int = 3000

    # 寒暄快路径（research D8；docs/02 §2.5 NFR：规则直答不进 LLM）
    chitchat_max_chars: int = 30

    # JWT（章程 V / ADR-009）
    jwt_secret: str = "dev-secret-change-me"
    jwt_exp_hours: int = 24


@lru_cache
def get_settings() -> Settings:
    return Settings()
