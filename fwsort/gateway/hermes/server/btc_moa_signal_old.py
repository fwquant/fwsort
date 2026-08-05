#!/usr/bin/env python3
"""BTC 15分钟方向信号生成器(MoA 三模型投票版,实时行情增强)。

三个模型(deepseek / kimi-k2.6 / MiniMax-M3)并行预测 BTC 未来15分钟
涨/跌/平。v2(2026-08-05): 提示词内附真实行情 —— 现价、5/15分钟涨跌、
24h涨跌、高/低、MA5、RSI(14)、近15分钟成交量,可选 Polymarket 市场定价。

默认只出信号(干跑),不碰 Polymarket。下单逻辑见 btc_moa_trader.py。

用法:
  python3 btc_moa_signal.py            # 干跑,打印信号
  python3 btc_moa_signal.py --json     # JSON 输出(供下游脚本调用)
"""
import functools
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor


# ---------- 读取密钥(Hermes 配置里已复用的三套) ----------
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
        "url": " ",
        "key": HERMES_ENV.get("DEEPSEEK_API_KEY", ""),
        "model": "deepseek-v4-flash",
        "temperature": 0.2,
        "max_tokens": 200,
    },
    {
        "name": "kimi-k2.6",
        "url": " ",
        "key": OPENCLAW_ENV.get("LLM_API_KEY", ""),
        "model": "kimi-k2.6",
        # kimi-k2.6 只允许 temperature=1,不传即用默认 1;
        # 思考块长,必须给足 token 否则 content 为空
        "max_tokens": 1500,
    },
    {
        "name": "minimax-m3",
        "url": " ",
        "key": CODEX_ENV.get("OPENAI_API_KEY", ""),
        "model": "MiniMax-M3",
        "temperature": 0.3,
        "max_tokens": 800,
    },
]
BASE_PROMPT = (
    "你是BTC短线交易分析师。请基于以下实时行情预判:未来15分钟BTC价格方向。\n"
    "只允许回答一个词:涨 或 跌 或 平(平=横盘震荡,涨跌幅小于0.1%)。\n"
    "不要解释,不要多余字符。"
)


def _rsi(closes, period=14):
    """简化 RSI(按老→新顺序的收盘价列表)。"""
    cs = list(reversed(closes))[: period + 1]
    if len(cs) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(1, len(cs)):
        d = cs[i] - cs[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    rs = (gains / period) / (losses / period)
    return round(100 - 100 / (1 + rs), 1)


@functools.lru_cache(maxsize=1)
def fetch_market_data():
    """实时行情(OKX 公开接口,进程内缓存一次)。

    返回 dict 或 None: price, chg_5m, chg_15m, chg_24h, high24h,
    low24h, ma5, rsi14, vol_15m。
    """
    data = {}
    try:
        req = urllib.request.Request(
            " ",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            t = json.loads(r.read().decode())["data"][0]
        last = float(t["last"])
        open24 = float(t["open24h"])
        data["price"] = last
        data["chg_24h"] = (last - open24) / open24 * 100 if open24 else 0.0
        data["high24h"] = float(t["high24h"])
        data["low24h"] = float(t["low24h"])
    except Exception:
        return None
    try:
        req = urllib.request.Request(
            " ",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            cand = json.loads(r.read().decode())["data"]  # 最新在前
        closes = [float(c[4]) for c in cand]
        p0, p5 = closes[0], closes[5] if len(closes) > 5 else closes[-1]
        p15 = closes[15] if len(closes) > 15 else closes[-1]
        data["chg_5m"] = (p0 - p5) / p5 * 100 if p5 else 0.0
        data["chg_15m"] = (p0 - p15) / p15 * 100 if p15 else 0.0
        data["ma5"] = sum(closes[:5]) / min(5, len(closes))
        data["rsi14"] = _rsi(closes)
        data["vol_15m"] = sum(float(c[5]) for c in cand[:15])
    except Exception:
        pass
    return data


def build_prompt(data=None):
    """按行情数据构造提示词;无数据时退回基础提示词。"""
    if not data:
        return BASE_PROMPT
    lines = ["你是BTC短线交易分析师。请基于以下实时行情预判:未来15分钟BTC价格方向。",
             "只允许回答一个词:涨 或 跌 或 平(平=横盘震荡,涨跌幅小于0.1%)。",
             "不要解释,不要多余字符。", "", "【实时行情】"]
    if data.get("price") is not None:
        lines.append(f"- 现价: ${data['price']:,.1f}")
    if data.get("chg_5m") is not None:
        lines.append(f"- 近5分钟涨跌: {data['chg_5m']:+.3f}%")
    if data.get("chg_15m") is not None:
        lines.append(f"- 近15分钟涨跌: {data['chg_15m']:+.3f}%")
    if data.get("chg_24h") is not None:
        lines.append(f"- 24小时涨跌: {data['chg_24h']:+.3f}%")
    if data.get("high24h") is not None:
        lines.append(f"- 24小时最高/最低: ${data['high24h']:,.0f} / ${data['low24h']:,.0f}")
    if data.get("ma5") is not None:
        lines.append(f"- 1分钟MA5: ${data['ma5']:,.1f}")
    if data.get("rsi14") is not None:
        lines.append(f"- RSI(14): {data['rsi14']}")
    if data.get("vol_15m") is not None:
        lines.append(f"- 近15分钟成交量: {data['vol_15m']:,.2f} BTC")
    if data.get("poly_up") is not None:
        lines.append(f"- Polymarket市场定价: UP {data['poly_up']:.1%} / DOWN {data['poly_down']:.1%}")
    return "\n".join(lines) + "\n"


def fetch_btc_price():
    """取当前 BTC 价格做参考(OKX 公开接口,国内可达)。失败返回 None。"""
    try:
        req = urllib.request.Request(
            " ",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode())
        return float(d["data"][0]["last"])
    except Exception:
        return None


def call_model(m, prompt=None):
    """调一个模型,返回 (模型名, 预测, 原始回复)。"""
    if not m["key"]:
        return m["name"], None, "缺少API key"
    if prompt is None:
        prompt = build_prompt(fetch_market_data())
    payload = {
        "model": m["model"],
        "messages": [
            {"role": "system", "content": "只输出一个中文字:涨/跌/平。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": m.get("max_tokens", 200),
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
        text = d["choices"][0]["message"]["content"]
        # 跳过思考块,取 </think> 之后的最终结论
        after = text.split("</think>")[-1]
        mch = re.search(r"涨|跌|平", after)
        return m["name"], (mch.group(0) if mch else None), text.strip()[:60]
    except Exception as e:
        return m["name"], None, f"错误: {e}"


def decide(results):
    """三票判定(用户规则):
    ① 任意两票 涨/跌 同向 -> 直接下单该方向
    ② 一涨一跌对峙 -> 第三票定夺(第三票 涨/跌 -> 下单;平/无效 -> 不下单)
    ③ 其余 -> 不下单
    返回 (signal, mode, counts, agreement)
    """
    counts = {}
    for v in ("涨", "跌", "平"):
        counts[v] = sum(1 for r in results if r[1] == v)
    signal = None
    mode = None
    for v in ("涨", "跌"):
        if counts[v] >= 2:
            signal, mode = v, "两票同向直接下单"
            break
    if signal is None and counts["涨"] == 1 and counts["跌"] == 1:
        third = next((r[1] for r in results if r[1] not in ("涨", "跌")), None)
        if third in ("涨", "跌"):
            signal, mode = third, "对峙由第三票定夺"
        else:
            mode = f"对峙但第三票为{third or '无效'},不下单"
    if signal is None and mode is None:
        mode = "无两票同向(平票过多或票型不足)"
    agreement = counts.get(signal, 0) if signal else 0
    return signal, mode, counts, agreement


def main():
    as_json = "--json" in sys.argv
    price = fetch_btc_price()
    data = fetch_market_data()
    prompt = build_prompt(data)
    results = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        for r in ex.map(lambda m: call_model(m, prompt), MODELS):
            results.append(r)
    signal, mode, counts, agreement = decide(results)
    out = {
        "ts": int(time.time()),
        "btc_price": price,
        "market_data": {k: v for k, v in (data or {}).items() if k not in ("poly_up", "poly_down")},
        "votes": {r[0]: r[1] for r in results},
        "raw": {r[0]: r[2] for r in results},
        "counts": counts,
        "signal": signal,
        "agreement": agreement,
        "mode": mode,
        "action": "ORDER" if signal else "NO_TRADE",
    }
    if as_json:
        print(json.dumps(out, ensure_ascii=False))
        return
    print(f"BTC 现价: {price if price else '获取失败'}")
    if data:
        print(f"行情: 5m={data.get('chg_5m', '?')}% 15m={data.get('chg_15m', '?')}% "
              f"RSI={data.get('rsi14', '?')} MA5={data.get('ma5', '?')}")
    for name, vote, raw in results:
        print(f"  {name:14} -> {vote or '无法解析'}  ({raw})")
    print(f"统计: 涨x{counts['涨']} 跌x{counts['跌']} 平x{counts['平']}")
    print(f"判定: {mode}")
    if signal:
        print(f"信号: {signal} -> 触发下单")


if __name__ == "__main__":
    main()
