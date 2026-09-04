# Phase 1 Quickstart: AgentLoop 端到端验证指南

> 目标：不读实现代码，跑通「规划式单轮 → 反思回环 → 多轮会话 → 幂等/并发 →
> 检查点恢复 → 收敛保险 → 回环评测」全链路，验证 spec 的 SC-001~008。
> 接口与事件细节见 [contracts/api.md](./contracts/api.md)，数据结构见
> [data-model.md](./data-model.md)。验证 1（入库）沿用阶段 1 quickstart，
> 此处不重复。

## 前置条件

- 阶段 1 环境就绪（Docker Compose、`.env`、已签发 TOKEN、条款库已入库）
- `uv sync` 已安装新增依赖（langgraph / langgraph-checkpoint-postgres / psycopg）

## 启动与建表

```bash
docker compose up -d api      # 重启后 lifespan 自动执行 checkpointer.setup()
uv run scripts/issue_token.py && export TOKEN=$(cat /tmp/atlas_token)
```

## 验证 1 · 规划式单轮问答（US1 / SC-004 事件面）

```bash
curl -sN -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"这款重疾险的等待期多久？等待期内出险赔吗？","client_msg_id":"c-1"}' \
  http://localhost:8000/v1/chat | tee /tmp/chat1.sse
# 期望事件序：(plan → tool_call* → evidence)* → answer* → citations → done
# plan.steps ≥ 2 且答案同时回应「等待期」与「等待期出险」两个子问题（SC-001 独立测试）
# done 含 convergence_reason/rounds/steps/tokens_used；记录 session_id 供验证 3
```

寒暄快路径（US1 场景 3 / SC-005）：

```bash
time curl -sN -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"你好"}' http://localhost:8000/v1/chat
# 期望首字节 <1s（模板回应，done.tokens_used=0，无 plan/tool_call 事件）
```

## 验证 2 · 反思回环（US2 / SC-002 口径演示）

```bash
# 用评测报告首轮失败案例中的问题提问（同配置首轮未命中）：
curl -sN -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"<首轮未命中的问题>","client_msg_id":"c-2"}' \
  http://localhost:8000/v1/chat
# 期望出现 round≥2 的 plan/evidence 事件，最终命中正确条款；
# 已执行步骤不重复（tool_call 的 query 与首轮不同的仅是新增/改写步骤）
# 再用一个持续无命中问题验证：3 轮后 done.refused=true、convergence_reason=max_steps 或 refused
```

## 验证 3 · 多轮会话（US4 / SC-006）

```bash
SESS=$(grep -o '"session_id":"[^"]*"' /tmp/chat1.sse | head -1 | cut -d'"' -f4)

# 追问（指代消解）：
curl -sN -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"question\":\"那它的宽限期呢？\",\"session_id\":\"$SESS\",\"client_msg_id\":\"c-3\"}" \
  http://localhost:8000/v1/chat
# 期望回答指向同一产品的宽限期条款（SC-006 双轮口径）

# 并发第二问（上一问进行中时立刻发）：
# 期望 409 {"code":"session_busy"}（FR-012）

# 幂等重放（同 client_msg_id 重发 c-3）：
# 期望 200 + replayed 事件流，与首次结果一致，runtime_log 无新行

# 历史查询与删除：
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/v1/sessions/$SESS
# 期望全部消息按序返回（含 citations）
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" http://localhost:8000/v1/sessions/$SESS  # 204
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/v1/sessions/$SESS            # 404
```

## 验证 4 · 检查点恢复（US5 / SC-008）

```bash
# 1) 发起一个长链路问题，在 answer 事件出现前 docker compose restart api
# 2) 重启完成后带相同 session_id + client_msg_id 重发：
#    期望从检查点续跑（已完成检索不重复，事件流继续到 done，无半截答案）
# 3) 损坏检查点（如手工删 checkpoints 行）后重发：
#    期望明确错误与重新开始指引，而非半截答案
```

## 验证 5 · 收敛保险（US3 / SC-003）

```bash
# 对抗输入逐条提交：诱导循环问题 / 超长问题（>500 字符应 422）/ 持续无命中问题
# 期望：每条都在上限内确定性停止；done.convergence_reason ∈
#   natural|max_steps|timeout|budget|refused；运行档案 steps≤6、tokens_used≤8000
```

## 验证 6 · 回环评测门禁（US2/SC-001/002）

```bash
python scripts/run_retrieval_eval.py --dataset data/evals/golden_qa_kaihe.clean.jsonl \
  --top-k 5 --use-rerank --loop --output report_loop.json
# 期望：
#   首轮指标（Recall@5 等）≥ 阶段 1 基线 − 2σ          （SC-001）
#   repair_rate = 回环修复数 ÷ 同配置首轮失败数 ≥ 0.3   （SC-002，clarify Q4）
#   报告含修复案例清单（问题/首轮 top5/回环后 top5）
```

## 验证 7 · 测试套件（章程 VII）

```bash
uv run pytest -q
# 全部通过；测试路径无真实 LLM/Embedding/Rerank 调用
#（LLM 为 FakeLLM 脚本化、检查点器连本地测试 PG——测试基座而非外部服务）
```

## 故障排查

| 现象 | 检查 |
|---|---|
| 重启后续跑 404 | 检查 session_id + client_msg_id 是否与中断前一致（幂等键不匹配 = 新消息） |
| 一直 409 | 上一请求未结束；或进程异常退出遗留 running——重启进程会复位为 interrupted |
| plan 事件缺失 | 命中寒暄快路径（正常）或 route=answer 直答（steps 为空） |
| 回环不触发 | 反思判据与阈值：确认 evidence 命中与 L4 校准口径，查看 runtime_log.plan_rounds |
| tokens_used=0 但有回答 | 命中寒暄模板路径（预期行为） |
