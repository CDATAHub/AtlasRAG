#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 kaihe 完整保险条款生成黄金问答对（阶段 0 · 路线 B 的第 1 步）。

设计目标：让「检索召回」评测有真实闭卷语料 + 配套 QA 标尺。

数据流：
  kaihe_clauses.jsonl (102 份完整条款)
    → 解析 output（公司名 / 产品名 / 章节结构）
    → 按章节分片（不切断章节，控制单次 LLM 输入 token）
    → 逐片调 LLM 抽取 QA（L0 事实 / L1 理解 / L3 数字推理，每条带 source 溯源）
    → 输出 golden_qa_kaihe.jsonl

LLM 接入（OpenAI 兼容，纯标准库 urllib，无第三方依赖）：
  用 DeepSeek / Qwen(dashscope) / OpenAI / 任意 OpenAI 兼容网关，通过环境变量配置：
    LLM_BASE_URL  默认 https://api.deepseek.com/v1（注意：须含 /v1 前缀）
    LLM_API_KEY   必填
    LLM_MODEL     默认 deepseek-chat

用法（DeepSeek）：
  export LLM_BASE_URL="https://api.deepseek.com/v1"
  export LLM_API_KEY="sk-xxx"
  export LLM_MODEL="deepseek-chat"

用法（通义千问 Qwen，dashscope 兼容模式）：
  export LLM_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
  export LLM_API_KEY="sk-xxx"
  export LLM_MODEL="qwen-plus"          # 或 qwen-max / qwen-turbo

  python generate_kaihe_qa.py --limit 5 --chunk-size 6000
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # AtlasRAG/data/
RAW = BASE / "raw" / "kaihe_clauses.jsonl"
OUT = BASE / "evals" / "golden_qa_kaihe.jsonl"
PROGRESS = BASE / "evals" / "generate_progress.json"

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
# 备用模型：主模型「额度用尽 / 欠费 / 限流」时自动降级（仅当与主模型不同才切换）
LLM_MODEL_FALLBACK = os.environ.get("LLM_MODEL_FALLBACK", "qwen3.7-flash-2026-07-15")

# ---- 抽取 Prompt ----
SYSTEM_PROMPT = (
    "你是一名资深保险条款问答对构造专家，专门为 RAG（检索增强生成）系统构造评测用问答对。"
    "你的每一条产出都会被用于自动化评分，因此必须严格可溯源、答案可判定。"
)

EXTRACT_PROMPT_TMPL = """给定一份保险条款的《{product}》部分章节，请从中抽取高质量的问答对。

【硬性要求】
1. 问题要口语化，模拟真实投保人/被保险人会问的话（如"等待期内确诊能赔吗"，而不是"请概述保险责任"）。
2. 答案必须能【仅凭给出的章节原文】直接判定，且要具体——含明确数字、日期、比例、金额、条件。
3. 每条必须能溯源：给出章节号 + 原文引用（quote 必须逐字来自下方原文，30 字以内）。
4. 难度分级（每条只能归入一类）：
   - L0 事实检索：答案是一个明确的名词/数字/日期，一眼能查到（如"等待期是多久"）。
   - L1 理解推理：需理解一个条件或规则后才能回答（如"等待期内确诊能赔吗？能赔多少？"）。
   - L3 数字推理：涉及百分比、金额、天数、限额、次数等的计算或比较（如"轻症赔付比例是重疾的多少倍""最高能赔多少"）。
5. 优先覆盖章节里的关键信息点：等待期、观察期、免赔额、赔付比例、责任免除、限额、生效条件、宽限期等。
6. 每片抽取 4~8 条，宁缺毋滥；无法溯源或答案含糊的不要写。

【输出格式】只输出一个 JSON 数组（不要 markdown 代码块、不要解释），每项：
{{"question": "...", "gold_answer": "...", "difficulty": "L0|L1|L3", "category": "主题标签(如等待期/免赔额/责任免除)", "source": {{"section": "章节号", "quote": "逐字原文引用"}}}}

【条款章节原文】
{chunk}
"""


def _is_quota_exhausted(e):
    """判断是否「额度用尽 / 欠费 / 限流」类错误，命中则触发降级切换模型。"""
    if isinstance(e, urllib.error.HTTPError):
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")
        except Exception:
            pass
        if e.code in (402, 403, 429):
            return True
        text = f"{e} {body}".lower()
        for kw in ("quota", "额度", "arrearage", "throttling", "exhausted",
                   "insufficient", "欠费", "余额", "rate limit", "rate_limit",
                   "too many requests", "overloaded"):
            if kw in text:
                return True
    return False


def llm_chat(messages, temperature=0.2, max_tokens=4000, retries=3):
    """OpenAI 兼容 chat/completions，纯 urllib 实现，带指数退避重试。
    主模型额度用尽 / 欠费 / 限流时自动永久降级到 LLM_MODEL_FALLBACK。"""
    global LLM_MODEL
    url = f"{LLM_BASE_URL}/chat/completions"
    last_err = None
    for attempt in range(retries):
        payload = json.dumps({
            "model": LLM_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=payload, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LLM_API_KEY}",
            })
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, IndexError,
                json.JSONDecodeError) as e:
            last_err = e
            # 额度用尽 / 欠费 / 限流 → 永久降级到备用模型，立即用新模型重试
            if (LLM_MODEL_FALLBACK and LLM_MODEL != LLM_MODEL_FALLBACK
                    and _is_quota_exhausted(e)):
                print(f"  [switch] 模型 {LLM_MODEL} 额度用尽/限流，永久切换为 {LLM_MODEL_FALLBACK}",
                      file=sys.stderr)
                LLM_MODEL = LLM_MODEL_FALLBACK
                continue
            wait = 2 ** attempt
            print(f"  [warn] LLM 调用失败(第{attempt + 1}次): {e}，{wait}s 后重试", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"LLM 调用最终失败: {last_err}")


def parse_clause(out: str):
    """解析 output → (company, product, sections)。

    sections: list[dict]，每个含 no(章节号)、title(标题)、text(正文)。
    约定：数字编号开头的行是标题，后续非空行归入当前章节正文。
    """
    lines = out.split("\n")
    company = lines[0].strip() if lines else ""
    product = lines[1].strip() if len(lines) > 1 else ""
    sections, cur = [], None
    title_re = re.compile(r"^(\d+(?:\.\d+)*)\s+(.+)$")
    for ln in lines[2:]:
        s = ln.strip()
        if not s:
            continue
        m = title_re.match(s)
        if m:
            cur = {"no": m.group(1), "title": m.group(2), "text": ""}
            sections.append(cur)
        elif cur is not None:
            cur["text"] += s  # 中文条款正文连续拼接，无需保留换行
    return company, product, sections


def chunk_sections(sections, chunk_size=6000):
    """把章节按字符数聚合成片，保证不切断单个章节。"""
    chunks, buf, buf_len = [], [], 0
    for sec in sections:
        block = f"{sec['no']} {sec['title']}\n{sec['text']}"
        if buf and buf_len + len(block) > chunk_size:
            chunks.append("\n\n".join(buf))
            buf, buf_len = [], 0
        buf.append(block)
        buf_len += len(block)
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def extract_json(raw: str):
    """从 LLM 输出中稳健地解析 JSON 数组（容忍 markdown 包裹、前后赘语）。"""
    raw = raw.strip()
    # 去掉 ```json ... ``` 包裹
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if m:
        raw = m.group(1).strip()
    # 定位第一个 [ 到最后一个 ]
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    raw = raw[start:end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 去掉尾逗号再试一次
        cleaned = re.sub(r",\s*([\]}])", r"\1", raw)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            print(f"  [warn] JSON 解析失败，跳过该片。原文前 200 字: {raw[:200]}", file=sys.stderr)
            return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def normalize_qa(item, company, product, doc_id):
    """规整单条 QA，保证字段齐全、类型合法。"""
    q = (item.get("question") or "").strip()
    a = (item.get("gold_answer") or "").strip()
    if not q or not a:
        return None
    diff = item.get("difficulty", "L1")
    if diff not in ("L0", "L1", "L3"):
        diff = "L1"
    src = item.get("source") or {}
    section = (src.get("section") or "").strip()
    quote = (src.get("quote") or "").strip()
    return {
        "id": None,  # 由校验脚本统一编号
        "question": q,
        "gold_answer": a,
        "difficulty": diff,
        "category": (item.get("category") or "其他").strip(),
        "source": [{"section": section, "quote": quote}],
        "metadata": {
            "doc_id": doc_id,
            "product": product,
            "company": company,
            "source_file": "kaihe_clauses",
            "difficulty_review": True,  # 需人工复核难度标签
        },
    }


def load_progress():
    if PROGRESS.exists():
        try:
            return json.loads(PROGRESS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"done_clauses": [], "total_qa": 0}


def save_progress(state):
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 份条款（0=全部）")
    ap.add_argument("--chunk-size", type=int, default=6000, help="单次 LLM 输入的分片字符数")
    ap.add_argument("--resume", action="store_true", help="断点续跑（跳过已完成的条款）")
    args = ap.parse_args()

    if not LLM_API_KEY:
        print("错误：请先设置环境变量 LLM_API_KEY（以及可选的 LLM_BASE_URL / LLM_MODEL）", file=sys.stderr)
        sys.exit(2)

    clauses = [json.loads(l) for l in RAW.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        clauses = clauses[:args.limit]

    # 非续跑：清空输出文件，从头开始
    if not args.resume:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text("", encoding="utf-8")

    # 断点续跑：从 OUT 文件内容推导已完成的条款（比 progress 文件更可靠，
    # 因为 progress 可能记录了"已处理但未落盘"的份，而 OUT 才是真实落盘状态）
    all_qa = []
    done = set()
    if args.resume and OUT.exists():
        all_qa = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines() if l.strip()]
        for qa in all_qa:
            md = qa["metadata"]
            done.add(f"{md['company']}|{md['product']}")
    state = {"done_clauses": sorted(done), "total_qa": len(all_qa)}

    for idx, clause in enumerate(clauses):
        company, product, sections = parse_clause(clause["output"])
        # 用公司+产品 作为唯一键（kaihe 内部无 id 字段）
        key = f"{company}|{product}"
        if args.resume and key in done:
            print(f"[{idx + 1}/{len(clauses)}] 跳过已处理: {product}")
            continue
        if not sections:
            print(f"[{idx + 1}/{len(clauses)}] 跳过(无章节): {product}")
            continue

        doc_id = re.sub(r"[^\w\u4e00-\u9fff]+", "_", product).strip("_") or f"doc_{idx}"
        chunks = chunk_sections(sections, args.chunk_size)
        new_items = []
        for ci, chunk in enumerate(chunks):
            prompt = EXTRACT_PROMPT_TMPL.format(product=product, chunk=chunk)
            raw = llm_chat([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ])
            items = extract_json(raw)
            for it in items:
                norm = normalize_qa(it, company, product, doc_id)
                if norm:
                    new_items.append(norm)

        # 每份处理完立即增量写入 OUT，中断/关机也不丢已处理内容
        with open(OUT, "a", encoding="utf-8") as f:
            for qa in new_items:
                f.write(json.dumps(qa, ensure_ascii=False) + "\n")
        all_qa.extend(new_items)

        print(f"[{idx + 1}/{len(clauses)}] {product}：{len(chunks)} 片 → +{len(new_items)} 条 QA")

        state["done_clauses"].append(key)
        state["total_qa"] = len(all_qa)
        save_progress(state)
        time.sleep(0.3)

    print(f"\n完成：共 {len(all_qa)} 条 QA，已写入 {OUT}")


if __name__ == "__main__":
    main()
