#!/usr/bin/env python3
"""通用 Hermes 调用器：接收任意 prompt → 调用多个 LLM → 返回 JSON 结果。

在 Ubuntu 上使用:
  python3 hermes_run.py --prompt "分析BTC趋势，预测15分钟方向" --symbol btc
  python3 hermes_run.py --prompt "ETH 4h 级别能涨吗？" --symbol eth --timeframe 4h
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def _load_env(path):
    env = {}
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


HERMES_ENV = _load_env(os.path.expanduser("~/.hermes/.env"))
OPENCLAW_ENV = _load_env(os.path.expanduser("~/.openagents/env/openclaw.env"))
CODEX_ENV = _load_env(os.path.expanduser("~/.openagents/env/codex.env"))

MODELS = [
    {
        "name": "deepseek",
        "url": "https://api.deepseek.com/v1/chat/completions",
        "key": HERMES_ENV.get("DEEPSEEK_API_KEY", ""),
        "model": "deepseek-v4-flash",
        "temperature": 0.2,
        "max_tokens": 500,
    },
    {
        "name": "kimi-k2.6",
        "url": "https://api.moonshot.cn/v1/chat/completions",
        "key": OPENCLAW_ENV.get("LLM_API_KEY", ""),
        "model": "kimi-k2.6",
        "max_tokens": 2000,
    },
    {
        "name": "minimax-m3",
        "url": "https://api.minimaxi.com/v1/chat/completions",
        "key": CODEX_ENV.get("OPENAI_API_KEY", ""),
        "model": "MiniMax-M3",
        "temperature": 0.3,
        "max_tokens": 1500,
    },
]


def call_model(m, prompt):
    if not m["key"]:
        return m["name"], None, "缺少API key"
    payload = {
        "model": m["model"],
        "messages": [
            {"role": "system", "content": "你是专业的量化交易分析师。请根据用户提供的prompt进行分析，给出明确的交易方向。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": m.get("max_tokens", 500),
    }
    if "temperature" in m:
        payload["temperature"] = m["temperature"]
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        m["url"], data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {m['key']}",
        })
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            d = json.loads(r.read().decode())
        text = d["choices"][0]["message"]["content"].strip()
        direction = _extract_direction(text)
        return m["name"], direction, text
    except Exception as e:
        return m["name"], None, f"错误: {e}"


def _extract_direction(text):
    if not text:
        return None
    t = text.lower()
    if any(w in t for w in ["涨", "up", "bull", "long", "买入", "多头"]):
        return "UP"
    if any(w in t for w in ["跌", "down", "bear", "short", "卖出", "空头"]):
        return "DOWN"
    if any(w in t for w in ["平", "震荡", "flat", "sideway"]):
        return "FLAT"
    return None


def _vote(results):
    counts = {"UP": 0, "DOWN": 0, "FLAT": 0}
    for _, d, _ in results:
        if d in counts:
            counts[d] += 1

    if counts["UP"] >= 2:
        signal, mode = "UP", "两票同向看涨"
    elif counts["DOWN"] >= 2:
        signal, mode = "DOWN", "两票同向看跌"
    elif counts["FLAT"] >= 2:
        signal, mode = "FLAT", "多数震荡"
    else:
        signal, mode = "", "无明确共识"
    return signal, mode, counts


def run_hermes(prompt, symbol="", timeframe=""):
    results = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        for r in ex.map(lambda m: call_model(m, prompt), MODELS):
            results.append(r)

    signal, mode, counts = _vote(results)

    analysis = {}
    for name, direction, raw in results:
        analysis[name] = {
            "direction": direction or "无效",
            "raw": raw[:200] if raw else "",
        }

    return {
        "success": True,
        "code": 0,
        "symbol": symbol,
        "timeframe": timeframe,
        "prompt": prompt,
        "direction": signal,
        "mode": mode,
        "counts": counts,
        "analysis": analysis,
        "timestamp": int(time.time()),
    }


def main():
    parser = argparse.ArgumentParser(description="通用 Hermes 调用器")
    parser.add_argument("--prompt", required=True, help="分析提示词")
    parser.add_argument("--symbol", default="", help="标的代码")
    parser.add_argument("--timeframe", default="", help="时间周期")
    parser.add_argument("--pretty", action="store_true", help="格式化输出")
    args = parser.parse_args()

    result = run_hermes(args.prompt, args.symbol, args.timeframe)

    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()