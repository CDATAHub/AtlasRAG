# Quickstart: Tool 层生产化验证

> 全部验证**零真实外部服务**（章程 VII）：不调 LLM / Embedding / Rerank；
> MCP 用本地演示 Server 与 in-memory transport。对应 spec SC-001~008 的
> mock/CI 验收口径（真实端到端评测按 clarify Q2 合并进 002-T045 债务批次）。

## 前置条件

- Python 3.12 + uv；Docker（PG，端口 5432）
- 依赖已装：`uv sync`（新增 `mcp` 包随本阶段引入）
- PG 起来并播种：`docker compose up -d` + 既有入库脚本（阶段 1 quickstart）

## 启动与建表

```bash
docker compose up -d
uv run python scripts/init_db.py     # 无新表（本阶段零库表变更），幂等
uv run python scripts/mcp_demo_server.py --version   # 演示 Server 可执行
```

## 验证 1：mock 测试全绿（SC-001/SC-008）

```bash
uv run pytest -q
# 期望：全部通过（既有 100 项零修改回归 + 本阶段新增：
# test_tools_executor / test_registry_scopes / test_mcp_client /
# test_parallel_step / test_doc_reader）
```

## 验证 2：执行引擎管道端到端（SC-002/003/004，零 LLM）

```bash
uv run python scripts/demo_tool_engine.py --scenario pipeline
# 期望输出（逐行断言式）：
#   [timeout]   挂起工具 5_000ms 上限内返回 error_class=timeout   ✓
#   [transient] 前败后成 attempts=2 重试成功                       ✓
#   [non-idem]  非幂等工具失败 attempts=1 零重发                   ✓
#   [validate]  非法参数进实现前拦截 error_class=validation        ✓
#   [recovered] 全部失败仅 failed 证据，循环不中断                  ✓
```

## 验证 3：权限收敛（SC-005，US2）

```bash
uv run python scripts/demo_tool_engine.py --scenario scopes
# 期望：
#   scopes=["retrieval:read"]   → 工具面 = [hybrid_search, doc_reader]
#   scopes=["misc:calc"]        → 工具面 = [mcp.* 演示工具]（不含检索）
#   scopes=[]                   → 工具面 = []（走既有降级路径）
#   越权直调 hybrid_search      → error_class=permission，实现未执行   ✓
```

## 验证 4：并行执行（SC-006，US4）

```bash
uv run python scripts/demo_tool_engine.py --scenario parallel
# 期望：同一步 2 调用（各 200ms）墙钟 <300ms（≤1.5x），结果顺序 = calls 顺序；
#       其一失败 → 另一成功结果保留、失败单独标 failed               ✓
```

## 验证 5：doc_reader 读取（US3，FR-012）

```bash
uv run python scripts/demo_tool_engine.py --scenario doc_reader
# 期望：doc_id → 全部章节（sec_no 有序）；(doc_id, sec_no) → 指定章节原文；
#       跨租户 doc_id → not_found；sec_no 不存在 → section_not_found    ✓
```

## 验证 6：MCP 统一调度（SC-007，US5）

```bash
# 终端 A：起演示 Server
uv run python scripts/mcp_demo_server.py
# 终端 B：经注册表调用（in-process 连 stdio Server）
uv run python scripts/demo_tool_engine.py --scenario mcp
# 期望：清单自动注册为 mcp.*；调用经同一管道（tool_call 带 attempts/duration_ms）；
#       停掉终端 A 后重跑 → mcp 工具从工具面消失、内置工具照常（FR-014）  ✓
```

## 验证 7：SSE 契约兼容（contracts/api.md v2.1）

```bash
uv run pytest tests/integration -q -k "chat or session"
# 期望：事件序与 002 契约一致；plan.steps[].calls / tool_call.attempts /
#       duration_ms 仅作为可选字段出现；旧事件断言零修改通过           ✓
```

## 故障排查

- **mcp 包缺失**：`uv sync`（pyproject 新增依赖后首次需重新同步）
- **验证 6 注册为空**：确认终端 A 的 Server 存活；应用日志应有
  `mcp source unavailable` warning 而非崩溃（FR-014 降级口径）
- **验证 1 既有用例失败**：优先排查 hybrid_search 是否被改动——本阶段
  其 invoke 逻辑必须零修改（SC-001 透传口径），仅允许新增 policy 声明
