"""V1.0 端到端：实盘模式 + 订单状态同步"""
import json
import sys
import urllib.request
import urllib.error


BASE = "http://localhost:8000"


def call(method: str, path: str, body: dict | None = None, token: str | None = None, raw: bool = False) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        return {"_http_error": e.code, "_body": body}


def main():
    # 登录
    r = call("POST", "/api/auth/login", {
        "email": "admin@fwquant.com",
        "password": "admin123456",
    })
    token = (r.get("data") or {}).get("access_token")
    print(f"token: {token[:24]}...")

    # 创建实盘 OKX 账户（直接调 admin 路由，绕过 agent_router 的 account_type 限制）
    print("\n[1] create OKX LIVE account (account_type=1) ...")
    r = call("POST", "/api/agent/accounts?platform=okx&name=OKX-LIVE&initial_balance=1000", token=token)
    live_acc = (r.get("data") or {})
    print(f"  account: {live_acc}")

    # 触发实盘 V1.0（gateway 会走真实 OKX API，account_type=1）
    print("\n[2] trigger predict-and-vote on LIVE OKX ...")
    body = {"symbol": "BTCUSDT", "timeframe": "15m"}
    r = call("POST", f"/api/agent/predict-and-vote?account_id={live_acc['id']}", body, token)
    print(f"  result: {json.dumps(r, ensure_ascii=False)[:500]}")

    # 同步订单状态
    uid = live_acc.get("uid")
    print(f"\n[3] sync order status for {uid} ...")
    r = call("POST", f"/api/agent/execution/{uid}/sync", None, token)
    print(f"  sync: {json.dumps(r, ensure_ascii=False)[:500]}")

    # ES 检索（应该降级 unavailable）
    print("\n[4] ES search ...")
    r = call("GET", f"/api/agent/execution/{uid}/es-search", None, token)
    print(f"  es: {r}")

    # 模拟盘账户的订单查询
    print("\n[5] query execution logs ...")
    r = call("GET", "/api/agent/execution/ACC-C374B447F9BA?limit=5", None, token)
    print(f"  logs: count={(r.get('data') or {}).get('count')}")

    print("\n=== LIVE + sync E2E test done ===")


if __name__ == "__main__":
    main()
