#!/usr/bin/env python3
"""BTC 15分钟方向信号生成器(MoA 三模型投票版,实时行情增强)。

三个模型(deepseek / kimi-k2.6 / MiniMax-M3)并行预测 BTC 未来15分钟
涨/跌/平。v3(2026-08-05): 支持 readme.md 标准 JSON 输出 + 历史累加。

用法:
  python3 btc_moa_signal.py              # 人类可读格式
  python3 btc_moa_signal.py --json       # 原始 JSON 格式
  python3 btc_moa_signal.py --readme     # readme.md 标准 JSON 格式（同时写入历史）
  python3 btc_moa_signal.py --show-history [--limit=50]  # 查看历史信号
  python3 btc_moa_signal.py --clear-history  # 清除历史文件
"""
import functools
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta


HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "btc_signal_history.jsonl")


def _append_to_history(signal: dict, history_file: str = HISTORY_FILE):
    try:
        with open(history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(signal, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[WARN] 写入历史文件失败: {e}", file=sys.stderr)


# ---------- 读取密钥 ----------
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
        "max_tokens": 200,
    },
    {
        "name": "kimi-k2.6",
        "url": "https://api.moonshot.cn/v1/chat/completions",
        "key": OPENCLAW_ENV.get("LLM_API_KEY", ""),
        "model": "kimi-k2.6",
        "max_tokens": 1500,
    },
    {
        "name": "minimax-m3",
        "url": "https://api.minimaxi.com/v1/chat/completions",
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
    data = {}
    try:
        req = urllib.request.Request(
            "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT",
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
            "https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=1m&limit=16",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            cand = json.loads(r.read().decode())["data"]
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
    if not data:
        return BASE_PROMPT
    lines = [
        "你是BTC短线交易分析师。请基于以下实时行情预判:未来15分钟BTC价格方向。",
        "只允许回答一个词:涨 或 跌 或 平(平=横盘震荡,涨跌幅小于0.1%)。",
        "不要解释,不要多余字符。", "", "【实时行情】",
    ]
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
    return "\n".join(lines) + "\n"


def fetch_btc_price():
    try:
        req = urllib.request.Request(
            "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode())
        return float(d["data"][0]["last"])
    except Exception:
        return None


def call_model(m, prompt=None):
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
        after = text.split(" response")[-1]
        mch = re.search(r"涨|跌|平", after)
        return m["name"], (mch.group(0) if mch else None), text.strip()[:60]
    except Exception as e:
        return m["name"], None, f"错误: {e}"


def decide(results):
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


def to_readme_format(raw_out: dict) -> dict:
    """将原始输出转换为 readme.md 要求的 JSON 格式"""
    votes = raw_out.get("votes", {})
    counts = raw_out.get("counts", {})
    signal = raw_out.get("signal", "")
    ts = raw_out.get("ts", int(time.time()))

    direction_map = {"涨": "UP", "跌": "DOWN", "平": "FLAT"}
    period = 15 * 60
    timestamp = (ts // period) * period

    analysis = {}
    for model, vote in votes.items():
        if vote in direction_map:
            analysis[model] = direction_map[vote]
        else:
            analysis[model] = vote if vote else "无效"

    order_direction = direction_map.get(signal, "") if signal else ""

    tz = timezone(timedelta(hours=8))
    start_dt = datetime.fromtimestamp(timestamp, tz)
    end_dt = datetime.fromtimestamp(timestamp + 900, tz)
    time_range = f"{start_dt.strftime('%Y-%m-%d %H:%M')}~{end_dt.strftime('%Y-%m-%d %H:%M')}"

    return {
        "success": True,
        "code": 0,
        "标的代码": f"btc-updown-15m-{timestamp}",
        "下单方向": order_direction,
        "msg": {
            "各模型分析结果": analysis,
            "时间范围": time_range,
            "其它扩展信息": (
                f"投票统计: 涨x{counts.get('涨', 0)} 跌x{counts.get('跌', 0)} "
                f"平x{counts.get('平', 0)}; 判定方式: {raw_out.get('mode', '')}"
            ),
        },

    }


def _run_models():
    """运行三个模型，返回原始结果"""
    price = fetch_btc_price()
    data = fetch_market_data()
    prompt = build_prompt(data)
    results = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        for r in ex.map(lambda m: call_model(m, prompt), MODELS):
            results.append(r)
    signal, mode, counts, agreement = decide(results)
    return {
        "ts": int(time.time()),
        "btc_price": price,
        "market_data": data,
        "votes": {r[0]: r[1] for r in results},
        "raw": {r[0]: r[2] for r in results},
        "counts": counts,
        "signal": signal,
        "agreement": agreement,
        "mode": mode,
        "action": "ORDER" if signal else "NO_TRADE",
    }


def main():
    """人类可读格式"""
    raw = _run_models()
    price = raw["btc_price"]
    data = raw["market_data"]
    results = [(k, raw["votes"][k], raw["raw"][k]) for k in raw["votes"]]

    print(f"BTC 现价: {price if price else '获取失败'}")
    if data:
        print(f"行情: 5m={data.get('chg_5m', '?')}% 15m={data.get('chg_15m', '?')}% "
              f"RSI={data.get('rsi14', '?')} MA5={data.get('ma5', '?')}")
    for name, vote, raw_text in results:
        print(f"  {name:14} -> {vote or '无法解析'}  ({raw_text})")
    print(f"统计: 涨x{raw['counts']['涨']} 跌x{raw['counts']['跌']} 平x{raw['counts']['平']}")
    print(f"判定: {raw['mode']}")
    if raw["signal"]:
        print(f"信号: {raw['signal']} -> 触发下单")


def main_json():
    """原始 JSON 格式"""
    raw = _run_models()
    out = raw.copy()
    if out.get("market_data"):
        out["market_data"] = {k: v for k, v in out["market_data"].items()
                              if k not in ("poly_up", "poly_down")}
    print(json.dumps(out, ensure_ascii=False))


def main_readme():
    """readme.md 标准 JSON 格式"""
    raw = _run_models()
    signal = to_readme_format(raw)
    line = json.dumps(signal, ensure_ascii=False, indent=2)
    print(line)
    _append_to_history(signal)


if __name__ == "__main__":
    if "--show-history" in sys.argv:
        limit = 20
        for arg in sys.argv[1:]:
            if arg.startswith("--limit="):
                limit = int(arg.split("=", 1)[1])
        try:
            records = []
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
            records.reverse()
            print(f"共 {len(records)} 条历史记录，显示最近 {limit} 条：\n")
            for i, r in enumerate(records[:limit]):
                print(f"  [{i+1}] {r.get('下单方向', '')} | {r.get('标的代码', '')} | {r.get('msg', {}).get('时间范围', '')}")
        except FileNotFoundError:
            print("历史文件不存在，尚未生成任何信号")
    elif "--clear-history" in sys.argv:
        try:
            os.remove(HISTORY_FILE)
            print("历史文件已清除")
        except FileNotFoundError:
            print("历史文件不存在")
    elif "--readme" in sys.argv:
        main_readme()
    elif "--json" in sys.argv:
        main_json()
    else:
        main()