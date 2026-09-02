# Phase 1 Quickstart: 端到端验证指南

> 目标：不读实现代码，跑通「入库 → 状态 → 带引用问答 → 拒答 → 检索评测」全链路，
> 验证 spec 的 SC-001/002/003/004。接口与事件细节见 [contracts/api.md](./contracts/api.md)，
> 数据结构见 [data-model.md](./data-model.md)。

## 前置条件

- Docker（Compose v2）与 Python 3.12
- `.env` 已配置（`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`，见 `.env.example`）
- 闭卷评测集就绪：`data/evals/golden_qa_kaihe.clean.jsonl`（985+51 条）

## 启动

```bash
docker compose up -d          # api + postgres(pgvector)
uv run scripts/issue_token.py # 签发演示 JWT（或 python scripts/issue_token.py）
export TOKEN=$(cat /tmp/atlas_token)
```

## 验证 1 · 入库与状态（US2 / SC-005）

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  -F "file=@data/corpus/doc-00265f64.txt;filename=充电桩综合保险条款.txt" \
  http://localhost:8000/v1/documents
# 期望 202 {"doc_id":"…","status":"processing"}

curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/v1/documents/<doc_id>/status
# 约 1~2 分钟内变为 {"status":"indexed","blocks":{"parents":N,"children":M}}
```

再传同一文件：期望 `200` + `X-Deduplicated: true`（同版本归并，FR-003）。

## 验证 2 · 带引用问答（US1 / SC-003/004）

```bash
curl -N -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"这款重疾险等待期多久？"}' \
  http://localhost:8000/v1/chat
# 期望事件序：evidence → answer(delta)* → citations → done
# citations[].quote 必须是条款原文；answer 中的 [n] 与 citations[].n 一一对应
```

把 `prototype/index.html` 的端点指向 `http://localhost:8000` 后打开页面，提问应能看到
与模拟演示一致的溯源效果。

## 验证 3 · 拒答不编造（US3 / SC-002）

```bash
curl -N -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"股票基金能保吗？"}' http://localhost:8000/v1/chat
# 期望 refused=true 的 done 事件 + 改进提问建议，且 citations 为空数组
```

## 验证 4 · 检索评测门禁（US4 / SC-001）

```bash
python scripts/run_retrieval_eval.py \
  --dataset data/evals/golden_qa_kaihe.clean.jsonl --top-k 5 --output report.json
# 期望（验收门槛）：
#   Recall@5（整体）≥ 0.8        （SC-001）
#   分难度报告：L0 / L1 / L3
#   L4 拒答率 ≥ 0.9              （SC-002，需先按拒答题校准阈值）
#   失败案例清单：每条含 问题 / 期望 quote / 实际 top5
```

## 验证 5 · 测试套件（章程 VII）

```bash
uv run pytest -q
# 全部通过；测试环境不产生任何真实外部调用（fake 客户端 + 夹具）
```

## 故障排查

| 现象 | 检查 |
|---|---|
| 上传后一直 processing | `docker compose logs api`；解析异常会转 failed 并记录 error |
| 问答 503 | 条款库为空或检索不可用（FR-010），先完成验证 1 |
| 401 | token 过期（exp），重新签发 |
| Recall@5 < 0.8 | 查看 report.json 失败案例清单，按 docs/06 四象限归因（数据/检索优先） |
