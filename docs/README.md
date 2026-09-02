# AtlasRAG — 生产级 Agentic RAG 平台 · 架构设计文档

> 以「知识检索」场景为切入点，用 AgentLoop 作为大脑、可插拔 Tool 层承载能力、评测闭环与可观测保证质量，最终把 Agent 从 Demo 稳定交付到真实产品。

## 一句话定位

AtlasRAG 是一个以**保险知识库智能问答**为首个场景的**生产级 Agentic RAG 平台**，核心不是「检索 + 生成」的固定流水线，而是一个**能自愈、可评测、可灰度、可审计的知识检索 Agent**。

## 为什么做这个项目

它面向「AI Agent 资深工程师」岗位 JD 的全部主线要求：

| JD 主线要求 | 本文档对应章节 |
|---|---|
| 场景闭环（用户输入 → 最终产出） | 01 · 02 |
| 生产级 AgentLoop（规划/状态/上下文/工具编排/收敛） | 03 |
| Tool Registry / Contract / Function Calling / MCP | 04 |
| RAG、向量检索、Docling、父子文档 | 05 |
| 评测闭环（EvalDataset / Replay / RAGAS / Judge / 回归） | 06 |
| 生产质量（Tracing / 指标 / 灰度 / 回滚 / 成本） | 06 |
| 数据底座、权限、租户隔离、驻留、留存、审计 | 07 |
| 服务接口、数据模型、代码工程结构 | 08 |
| 里程碑路线图与验收标准 | 09 |

## 文档目录

| 文档 | 内容 |
|---|---|
| [01-项目概述与JD覆盖矩阵](01-项目概述与JD覆盖矩阵.md) | 背景、定位、设计原则、JD 逐条映射、范围与非目标 |
| [02-总体架构](02-总体架构.md) | 六层架构、技术选型、关键设计决策（ADR）、请求生命周期 |
| [03-AgentLoop详细设计](03-AgentLoop详细设计.md) | 状态机、State Schema、各节点设计、收敛策略、上下文管理 |
| [04-Tool层设计](04-Tool层设计.md) | Tool Registry、Contract、执行引擎、MCP、内置工具清单 |
| [05-RAG数据管道设计](05-RAG数据管道设计.md) | Docling 解析、切分、混合检索、重排、父子文档、索引流水线 |
| [06-评测闭环与生产质量](06-评测闭环与生产质量.md) | EvalDataset、Replay、RAGAS/Judge、Tracing、灰度、成本 |
| [07-数据底座合规与数据模型](07-数据底座合规与数据模型.md) | 租户隔离、权限、驻留、留存、审计、表结构、ER |
| [08-API与代码目录结构](08-API与代码目录结构.md) | REST/SSE 接口契约、代码目录、配置、部署拓扑 |
| [09-里程碑路线图与验收标准](09-里程碑路线图与验收标准.md) | 六阶段路线图、每阶段交付物与验收、取舍与风险 |

> 本目录是**唯一事实源**。根目录的 `AtlasRAG-架构设计文档.md` 为脚本生成的合并产物（`python3 scripts/build_combined_doc.py`），仅供单文件分享，**勿手改**。阶段 0 数据成果见 [`data/README.md`](../data/README.md)。

## 快速导读

- **只想了解全貌** → 先读 01、02，再看 09 的路线图。
- **想深入 Agent 内核** → 读 03（AgentLoop）和 04（Tool 层）。
- **想深入检索质量** → 读 05（RAG 管道）。
- **想体现工程深度** → 读 06（评测 + 生产质量）和 07（合规）。

## 技术栈速览

| 模块 | 选型 |
|---|---|
| Agent Runtime | LangGraph（Python） |
| 服务框架 | FastAPI + SSE 流式 |
| 文档解析 | Docling（布局分析 + 表格识别） |
| 向量库 | pgvector（或 Qdrant） |
| Embedding / 重排 | 百炼 qwen3.7-text-embedding / qwen3.7-text-rerank |
| 混合检索 | 向量 + BM25（Postgres tsvector）+ 重排 |
| 评测 | RAGAS + 自研 LLM-as-Judge |
| 可观测 | Langfuse（Tracing / 指标 / 成本） |
| LLM | 可插拔，默认 Qwen（qwen3.7-flash / qwen3.7-max） |
