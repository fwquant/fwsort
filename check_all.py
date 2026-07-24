import urllib.request
import json
import sys

BASE = "http://localhost:8000"

# 1. 登录
print("=== 1. 登录 ===")
login_req = urllib.request.Request(
    f"{BASE}/api/auth/login",
    data=json.dumps({"email": "admin@fwquant.com", "password": "admin123456"}).encode("utf-8"),
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(login_req) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    token = data["data"]["access_token"]
    me = data["data"]
    print(f"  OK user_id={me['user_id']} nickname={me['nickname']} role={me['role']}")

auth = {"Authorization": f"Bearer {token}"}

# 2. /me
print("=== 2. /me ===")
with urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/auth/me", headers=auth)) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    print(f"  OK id={data['data']['id']} email={data['data']['email']}")

# 3. 榜单列表（5种）
print("=== 3. 榜单列表 ===")
for rt in ["realtime", "daily", "weekly", "monthly", "all_time"]:
    with urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/ranking/list?rank_type={rt}&page_size=5", headers=auth)) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        d = data["data"]
        print(f"  {rt}: total={d['total']} items={len(d['items'])} top1={d['items'][0]['name']} score={d['items'][0]['composite_score']} tier={d['items'][0]['tier']}")

# 4. 我的账户
print("=== 4. 我的账户 ===")
# 没有 /api/accounts/list，需要查 router
# 试一下
for path in ["/api/accounts", "/api/accounts/list", "/api/account", "/api/account/list"]:
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{BASE}{path}", headers=auth)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"  GET {path}: {data}")
    except urllib.error.HTTPError as e:
        print(f"  GET {path}: HTTP {e.code}")

# 5. 我的跟单
print("=== 5. 我的跟单 ===")
for path in ["/api/follow/my", "/api/follow/subscriptions"]:
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{BASE}{path}", headers=auth)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"  GET {path}: keys={list(data.get('data', {}).keys()) if data.get('data') else data}")
    except urllib.error.HTTPError as e:
        print(f"  GET {path}: HTTP {e.code}")

# 6. 智能体租用品类
print("=== 6. 智能体租用品类 ===")
with urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/rental/agents")) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    print(f"  公开列表: count={data['data']['count']}")
    for a in data['data']['agents'][:3]:
        print(f"    id={a['id']} name={a['name']} model={a['model']} call=${a['price_per_call']} hour=${a['price_per_hour']}")

# 7. 我的租用
print("=== 7. 我的租用 ===")
with urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/rental/my", headers=auth)) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    print(f"  count={data['data']['count']} rentals={data['data']['rentals']}")

# 8. 通知
print("=== 8. 通知 ===")
for path in ["/api/notify/list?unread_only=true", "/api/notify/list"]:
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{BASE}{path}", headers=auth)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"  GET {path}: keys={list(data.get('data', {}).keys()) if data.get('data') else data}")
    except urllib.error.HTTPError as e:
        print(f"  GET {path}: HTTP {e.code}")

# 9. 触发投票（V1.0 核心）
print("=== 9. 触发 V1.0 投票（取第一个账户）===")
# 拿第一个账户的 uid
with urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/ranking/list?page_size=1")) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    top_uid = data['data']['items'][0]['uid']
    print(f"  使用 uid={top_uid}")

for ep in ["/api/agent/predict", "/api/agent/vote", "/api/agent/predict-and-vote", "/api/vote"]:
    try:
        req = urllib.request.Request(
            f"{BASE}{ep}?uid={top_uid}",
            data=json.dumps({"symbol": "BTCUSDT", "timeframe": "15m"}).encode("utf-8"),
            headers={**auth, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"  POST {ep}: {json.dumps(data, ensure_ascii=False)[:300]}")
    except urllib.error.HTTPError as e:
        print(f"  POST {ep}: HTTP {e.code}")
    except Exception as e:
        print(f"  POST {ep}: ERROR {e}")

print("=== OK ===")
