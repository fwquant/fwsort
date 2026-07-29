"""smoke 测试：登录 → 主流程 API 验证
需要服务在 127.0.0.1:8000 已启动
"""
import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"


def post(path, data=None, headers=None, is_json=False):
    if is_json:
        body = json.dumps(data).encode()
        h = {"Content-Type": "application/json", **(headers or {})}
    else:
        body = urllib.parse.urlencode(data or {}).encode()
        h = headers or {}
    req = urllib.request.Request(BASE + path, data=body, headers=h, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def get(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def put(path, data, headers):
    return post(path, data, headers, is_json=True)


def delete(path, headers):
    req = urllib.request.Request(BASE + path, headers=headers, method="DELETE")
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def main():
    # 1) 登录
    r = post("/api/auth/login", {"email": "admin@fwquant.com", "password": "admin123456"}, is_json=True)
    print(f"[1] login: success={r.get('success')}")
    token = r["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2) /me
    me = get("/api/auth/me", headers)["data"]
    print(f"[2] me: {me['email']} share={me['share_to_global']} allow_follow={me['allow_follow']}")

    # 3) GET / POST privacy
    print(f"[3] privacy get: {get('/api/auth/privacy', headers)['data']}")
    print(f"[4] privacy toggle: {post('/api/auth/privacy', {'allow_follow': False}, headers, is_json=True)['data']}")
    print(f"[5] privacy restore: {post('/api/auth/privacy', {'allow_follow': True}, headers, is_json=True)['data']}")

    # 4) agent tasks
    tasks = get("/api/agent/tasks", headers)["data"]["tasks"]
    print(f"[6] tasks count: {len(tasks)}")
    for t in tasks[:3]:
        print(f"     - {t['task']:30s} status={t['status']} last_run={t['last_run_at']}")

    # 5) 我的账户
    accts = get("/api/agent/accounts", headers)["data"]
    print(f"[7] accounts: {accts['count']}")
    if accts["count"] > 0:
        a = accts["accounts"][0]
        print(f"     first: {a['uid']} signal={a['signal']} public={a['public_enabled']} symbol={a['target_symbol']} amount={a['order_amount_usd']}")

    # 6) 创建带 URL 的账户
    r = post("/api/agent/accounts", {
        "name": "Smoke测试账户", "platform": "okx", "initial_balance": 2000,
        "target_url": "https://www.okx.com/trade-spot/eth-usdt",
        "order_amount_usd": 50, "public_enabled": True,
    }, headers)
    print(f"[8] created: {r['data']['uid']} symbol={r['data'].get('target_symbol')}")
    new_id = r["data"]["id"]

    # 7) 编辑（关闭 public + 改金额）
    r = put(f"/api/agent/accounts/{new_id}", {"public_enabled": False, "order_amount_usd": 100}, headers)
    print(f"[9] updated: public={r['data']['public_enabled']} amount={r['data']['order_amount_usd']}")

    # 8) 刷新信号
    r = post(f"/api/agent/accounts/{new_id}/signal/refresh?source=random", {}, headers, is_json=True)
    print(f"[10] signal: {r['data']['signal']}")

    # 9) 删除
    r = delete(f"/api/agent/accounts/{new_id}", headers)
    print(f"[11] deleted: {r}")

    # 10) 触发任务（受限任务）
    r = post("/api/agent/tasks/refresh_account_signals/trigger", {}, headers, is_json=True)
    print(f"[12] trigger task: {r['message']} task_id={r['data']['task_id']}")

    # 11) 非法任务触发
    try:
        r = post("/api/agent/tasks/unknown/trigger", {}, headers, is_json=True)
        print(f"[13] invalid task: SHOULD have raised")
    except urllib.error.HTTPError as e:
        print(f"[13] invalid task rejected: HTTP {e.code}")

    print("\n=== ALL SMOKE OK ===")


if __name__ == "__main__":
    main()
