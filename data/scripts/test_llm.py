#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 连通性测试：验证 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 是否可用。
用法同 generate_kaihe_qa.py，成功会打印模型返回的一句回答。
"""
import json
import os
import sys
import urllib.request

BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
API_KEY = os.environ.get("LLM_API_KEY", "")
MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")


def main():
    if not API_KEY:
        print("错误：未设置 LLM_API_KEY", file=sys.stderr)
        sys.exit(2)
    url = f"{BASE_URL}/chat/completions"
    payload = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": "请用一句话回复：连接成功。"}],
        "max_tokens": 50,
    }).encode("utf-8")
    print(f"目标：{url}\n模型：{MODEL}\n")
    try:
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        print("✅ 连通成功，模型回复：")
        print("  ", data["choices"][0]["message"]["content"].strip())
    except Exception as e:
        print(f"❌ 失败：{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
