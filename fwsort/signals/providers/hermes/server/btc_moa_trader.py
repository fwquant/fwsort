#!/usr/bin/env python3
"""BTC MoA 信号 -> Polymarket 下单(CLOB V2 + 存款钱包版)。

规则(用户定义,2026-08-05 固定):
  ① 任意两个智能体 涨/跌 同向 -> 直接下单该方向
  ② 一涨一跌对峙 -> 以第三个智能体的预测为准下单
  ③ 其余 -> 不下单
  市场: 默认 MARKET_ID=3327945(关闭时自动跟随最新 BTC 15分钟滚动市场)
  金额: 每单 $5(FAK 市价单,滑点上限 5%)

默认 DRY-RUN。加 --live 才真下单。运行: ~/poly-venv/bin/python btc_moa_trader.py [--live]

凭据: ~/.wallet.env(WALLET_PRIVATE_KEY,存款钱包 EOA) + ~/.polymarket.env(配置)
"""
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import btc_moa_signal as sig

WALLET_ENV = os.path.expanduser("~/.wallet.env")
CONF_ENV = os.path.expanduser("~/.polymarket.env")
LOG_FILE = os.path.expanduser("~/btc-moa-trades.log")
GAMMA = "https://gamma-api.polymarket.com"
FUNDER = "0x15c2488eb36f73736daf93dfbadaaebde8f5ff1a"  # 存款钱包(抵押代理,owner=EOA)

LIVE = "--live" in sys.argv


def load_env(path):
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


def load_creds():
    creds = {}
    creds.update(load_env(CONF_ENV))
    creds.update(load_env(WALLET_ENV))  # wallet.env 覆盖(新钥匙优先)
    return creds


def _gamma_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _latest_rolling_market():
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    for i in range(4):
        mins = ((now.minute // 15) + i) * 15
        start = now.replace(minute=mins % 60, second=0, microsecond=0)
        if mins >= 60:
            start += datetime.timedelta(hours=mins // 60)
        slug = f"btc-updown-15m-{int(start.timestamp())}"
        try:
            d = _gamma_get(f"{GAMMA}/markets?slug={slug}")
            if isinstance(d, dict):
                d = d.get("markets", d.get("data", []))
            for m in d:
                if m.get("closed"):
                    continue
                tokens = json.loads(m.get("clobTokenIds") or "[]")
                if len(tokens) == 2:
                    prices = json.loads(m.get("outcomePrices") or '["0.5","0.5"]')
                    return (m.get("question"), m.get("conditionId"),
                            tokens[0], tokens[1], float(prices[0]), float(prices[1]))
        except Exception:
            continue
    raise RuntimeError("未找到活跃的 BTC 15分钟滚动市场")


def _current_slug():
    """当前(含未来3个)活跃 BTC 15分钟窗口 slug,用作信号返回的"标的代码"。"""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    for i in range(4):
        mins = ((now.minute // 15) + i) * 15
        start = now.replace(minute=mins % 60, second=0, microsecond=0)
        if mins >= 60:
            start += datetime.timedelta(hours=mins // 60)
        slug = f"btc-updown-15m-{int(start.timestamp())}"
        try:
            d = _gamma_get(f"{GAMMA}/markets?slug={slug}")
            if isinstance(d, dict):
                d = d.get("markets", d.get("data", []))
            for m in d:
                if not m.get("closed"):
                    return slug
        except Exception:
            continue
    return f"btc-updown-15m-{int(now.timestamp())}"


def find_btc_market(creds):
    """MARKET_ID > MARKET_SLUG > 最新滚动市场。"""
    mid = creds.get("MARKET_ID", "").strip()
    slug = creds.get("MARKET_SLUG", "").strip()
    if mid:
        try:
            m = _gamma_get(f"{GAMMA}/markets/{mid}")
            tokens = json.loads(m.get("clobTokenIds") or "[]")
            if not m.get("closed") and len(tokens) == 2:
                prices = json.loads(m.get("outcomePrices") or '["0.5","0.5"]')
                return (m.get("question"), m.get("conditionId"),
                        tokens[0], tokens[1], float(prices[0]), float(prices[1]))
            print(f"  市场 {mid} 已关闭,自动跟随最新活跃市场", file=sys.stderr)
        except Exception:
            print(f"  市场 {mid} 获取失败,自动跟随最新活跃市场", file=sys.stderr)
    if slug:
        d = _gamma_get(f"{GAMMA}/markets?slug={slug}")
        if isinstance(d, dict):
            d = d.get("markets", d.get("data", []))
        if d:
            m = d[0]
            tokens = json.loads(m.get("clobTokenIds") or "[]")
            prices = json.loads(m.get("outcomePrices") or '["0.5","0.5"]')
            return (m.get("question"), m.get("conditionId"),
                    tokens[0], tokens[1], float(prices[0]), float(prices[1]))
    return _latest_rolling_market()


def _place_via_pyclob(creds, market, direction, size_usdc):
    """CLOB V2 下单: 存款钱包(POLY_1271) + FAK 市价单。"""
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import MarketOrderArgsV2, OrderType
    from py_clob_client_v2.order_utils import SignatureTypeV2

    key = creds.get("WALLET_PRIVATE_KEY") or creds.get("POLY_PRIVATE_KEY")
    client = ClobClient(
        "https://clob.polymarket.com", chain_id=137, key=key,
        signature_type=SignatureTypeV2.POLY_1271, funder=FUNDER,
    )
    client.set_api_creds(client.derive_api_key())

    token_yes, token_no = market[2], market[3]
    token_id = token_yes if direction == "涨" else token_no

    try:
        last = float(client.get_last_trade_price(token_id))
    except Exception:
        last = 0.50
    cap = min(0.99, round(last * 1.05, 3))
    resp = client.create_and_post_market_order(
        MarketOrderArgsV2(token_id=token_id, amount=size_usdc, side="BUY", price=cap),
        order_type=OrderType.FAK)
    return resp


def already_ordered(market):
    """该市场(按 condition_id)是否已下过单 —— 每市场只交易1单(用户规则)。"""
    try:
        for line in open(LOG_FILE, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("order") == "PLACED" and (
                    d.get("condition_id") == market[1]
                    or d.get("market") == market[0]):
                return True
    except FileNotFoundError:
        pass
    return False


def main():
    as_json = "--json" in sys.argv
    creds = load_creds()
    price = sig.fetch_btc_price()

    # 先找市场(拿 Polymarket 定价喂给模型,同时作下单目标)
    market = None
    try:
        market = find_btc_market(creds)
    except Exception as e:
        print(f"  市场查找失败(将只用行情数据投票): {e}", file=sys.stderr)

    data = sig.fetch_market_data() or {}
    if market and len(market) >= 6 and market[4] is not None:
        data = dict(data)
        data["poly_up"] = market[4]
        data["poly_down"] = market[5]
    prompt = sig.build_prompt(data)

    results = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        for r in ex.map(lambda m: sig.call_model(m, prompt), sig.MODELS):
            results.append(r)
    signal, mode, counts, agreement = sig.decide(results)

    entry = {
        "ts": int(time.time()),
        "btc_price": price,
        "votes": {r[0]: r[1] for r in results},
        "counts": counts,
        "signal": signal,
        "mode": mode,
        "action": "ORDER" if signal else "NO_TRADE",
        "live": LIVE,
        "market_data": {k: v for k, v in (data or {}).items() if k not in ("poly_up", "poly_down")},
    }
    if market:
        entry["market"] = market[0]
        entry["condition_id"] = market[1]

    if signal:
        key = creds.get("WALLET_PRIVATE_KEY") or creds.get("POLY_PRIVATE_KEY")
        if not LIVE:
            entry["order"] = "DRY_RUN"
            entry["note"] = "干跑模式,未下单(--live 才真下单)"
        elif not key:
            entry["order"] = "SKIPPED"
            entry["note"] = "缺少 WALLET_PRIVATE_KEY(见 ~/.wallet.env)"
        elif not market:
            entry["order"] = "SKIPPED"
            entry["note"] = "未找到目标市场,不下单"
        else:
            try:
                if already_ordered(market):
                    entry["order"] = "SKIPPED"
                    entry["note"] = "该市场已下过单(每市场仅1单)"
                else:
                    size = float(creds.get("ORDER_SIZE_USDC", "5"))
                    resp = _place_via_pyclob(creds, market, signal, size)
                    entry["order"] = "PLACED"
                    entry["response"] = json.loads(json.dumps(resp, default=str))
            except Exception as e:
                entry["order"] = "ERROR"
                entry["note"] = str(e)[:300]

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    if as_json:
        # 用户约定返回格式: 标的代码(滚动窗口 slug) + 下单方向(UP/DOWN/NO_TRADE)
        print(json.dumps({
            "标的代码": _current_slug(),
            "下单方向": {"涨": "UP", "跌": "DOWN"}.get(signal, "NO_TRADE"),
        }, ensure_ascii=False))
        return

    print(f"[{time.strftime('%H:%M')}] BTC={price if price else '?'} MoA 投票: "
          + " ".join(f"{k}={v}" for k, v in entry["votes"].items())
          + f" | 判定: {mode}")
    if signal:
        print(f"  → 信号 {signal}: {entry.get('order', '?')}"
              + (f" ({entry.get('note', '')})" if entry.get("note") else "")
              + (f" | market={entry.get('market')}" if entry.get("market") else ""))
    else:
        print("  → 不交易")


if __name__ == "__main__":
    main()
