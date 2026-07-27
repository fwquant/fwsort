"""V1.0 端到端 API 测试：登录 → 创建账户 → 预测+投票+下单 → 查询日志"""
import json
import sys
import urllib.request
import urllib.parse
import urllib.error


BASE = "http://localhost:8000"


def call(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
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
    # 1) 登录（用上次测试的 admin token）
    print("[1] login admin@fwquant.com ...")
    r = call("POST", "/api/auth/login", {
        "email": "admin@fwquant.com",
        "password": "admin123456",
    })
    if r.get("success") is False:
        # 注册管理员
        print("  no admin, try register ...")
        r = call("POST", "/api/auth/register", {
            "email": "admin@fwquant.com",
            "password": "admin123456",
            "nickname": "admin",
        })
        print(f"  register: {r}")
        r = call("POST", "/api/auth/login", {
            "email": "admin@fwquant.com",
            "password": "admin123456",
        })
    token = (r.get("data") or {}).get("access_token")
    print(f"  token: {token[:24] if token else 'NONE'}...")
    if not token:
        print("FATAL: no token")
        sys.exit(1)

    # 2) 创建 OKX 模拟账户
    print("[2] create OKX simulator account ...")
    r = call("POST", "/api/agent/accounts?platform=okx&name=Test-OKX&initial_balance=1000", token=token)
    print(f"  create: {r}")
    acc_id = (r.get("data") or {}).get("id")
    if not acc_id:
        print("FATAL: no account id")
        sys.exit(1)

    # 3) 触发 V1.0 预测+投票+下单
    print("[3] trigger predict-and-vote ...")
    body = {"symbol": "BTCUSDT", "timeframe": "15m"}
    r = call("POST", f"/api/agent/predict-and-vote?account_id={acc_id}", body, token)
    print(f"  result: {json.dumps(r, ensure_ascii=False)[:600]}")

    # 4) 创建 Polymarket 模拟账户
    print("[4] create Polymarket simulator account ...")
    r = call("POST", "/api/agent/accounts?platform=polymarket&name=Test-Poly&initial_balance=500", token=token)
    poly_acc_id = (r.get("data") or {}).get("id")
    print(f"  create: poly_acc_id={poly_acc_id}")

    # 5) 再次触发 V1.0（polymarket）
    if poly_acc_id:
        print("[5] trigger predict-and-vote on polymarket ...")
        r = call("POST", f"/api/agent/predict-and-vote?account_id={poly_acc_id}", body, token)
        print(f"  result: {json.dumps(r, ensure_ascii=False)[:600]}")

    # 6) 查执行账户列表
    print("[6] list my accounts ...")
    r = call("GET", "/api/agent/accounts", token=token)
    print(f"  count: {(r.get('data') or {}).get('count')}")

    print("=== V1.0 E2E test done ===")


if __name__ == "__main__":
    main()
