import urllib.request
import json

BASE = "http://localhost:8000"

# 登录
login_req = urllib.request.Request(
    f"{BASE}/api/auth/login",
    data=json.dumps({"email": "admin@fwquant.com", "password": "admin123456"}).encode("utf-8"),
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(login_req) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    token = data["data"]["access_token"]

auth = {"Authorization": f"Bearer {token}"}

# 1. 我的账户
print("=== 1. GET /api/agent/accounts ===")
with urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/agent/accounts", headers=auth)) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    print(f"  count={data['data']['count']}")
    for a in data['data']['accounts'][:3]:
        print(f"    id={a['id']} uid={a['uid']} name={a['name']} platform={a['platform']} balance={a['current_balance']}")

# 2. 创建账户
print("=== 2. POST /api/agent/accounts ===")
import urllib.parse
qs = urllib.parse.urlencode({"name": "测试账户-前端验证", "platform": "okx", "initial_balance": 1500})
req = urllib.request.Request(f"{BASE}/api/agent/accounts?{qs}", method="POST", headers=auth)
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    print(f"  result: {data}")
    new_id = data['data']['id']

# 3. 触发投票（account_id 是 query param）
print(f"=== 3. POST /api/agent/predict-and-vote?account_id={new_id} ===")
req = urllib.request.Request(
    f"{BASE}/api/agent/predict-and-vote?account_id={new_id}",
    data=json.dumps({"symbol": "BTCUSDT", "timeframe": "15m"}).encode("utf-8"),
    headers={**auth, "Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print(f"  vote: up={data['data']['up_count']} down={data['data']['down_count']} dir={data['data']['final_direction']} amt=${data['data']['order_amount_usd']} reason={data['data']['reason']}")
        print(f"  predictions: {len(data['data']['predictions'])}")
        for p in data['data']['predictions']:
            print(f"    {p['agent_name']}({p['agent_model']}): dir={p['direction']} conf={p['confidence']}")
        print(f"  order_id={data['data']['order_id']} order_status={data['data']['order_status']}")
except urllib.error.HTTPError as e:
    print(f"  HTTP {e.code}: {e.read().decode('utf-8')}")

# 4. 删除刚创建的账户
print(f"=== 4. DELETE /api/agent/accounts/{new_id} ===")
req = urllib.request.Request(f"{BASE}/api/agent/accounts/{new_id}", method="DELETE", headers=auth)
try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print(f"  result: {data}")
except urllib.error.HTTPError as e:
    print(f"  HTTP {e.code}: {e.read().decode('utf-8')}")

# 5. 跟单
print("=== 5. 跟单 ===")
with urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/follow/market?rank_type=all_time&limit=5", headers=auth)) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    print(f"  market: count={data['data']['count']} top={data['data']['items'][0]['leader_name'] if data['data'].get('items') else 'N/A'}")

# 6. 跟单订阅
print("=== 6. 跟单订阅 ===")
qs = urllib.parse.urlencode({"leader_uid": "MOCK-0001", "leader_name": "测试", "mode": 1, "subscription_fee_usd": 9.9})
req = urllib.request.Request(f"{BASE}/api/follow/subscribe?{qs}", method="POST", headers=auth)
try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print(f"  subscribe: {data}")
except urllib.error.HTTPError as e:
    print(f"  HTTP {e.code}: {e.read().decode('utf-8')}")

# 7. 智能体租用 - 按次
print("=== 7. rental call ===")
qs = urllib.parse.urlencode({"agent_id": 1, "symbol": "BTCUSDT", "timeframe": "15m"})
req = urllib.request.Request(f"{BASE}/api/rental/call?{qs}", method="POST", headers=auth)
try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print(f"  call: {data}")
except urllib.error.HTTPError as e:
    print(f"  HTTP {e.code}: {e.read().decode('utf-8')}")

# 8. 智能体租用 - 包时段
print("=== 8. rental rent (24h) ===")
qs = urllib.parse.urlencode({"agent_id": 1, "hours": 24})
req = urllib.request.Request(f"{BASE}/api/rental/rent?{qs}", method="POST", headers=auth)
try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print(f"  rent: {data}")
except urllib.error.HTTPError as e:
    print(f"  HTTP {e.code}: {e.read().decode('utf-8')}")

# 9. 我的租用
print("=== 9. my rentals ===")
with urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/rental/my", headers=auth)) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    print(f"  count={data['data']['count']}")
    for r in data['data']['rentals'][:3]:
        print(f"    id={r['id']} agent={r['agent_name']} type={r['rental_type']} paid=${r['total_paid_usd']}")

# 10. 通知
print("=== 10. notify list ===")
with urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/notify/list?only_unread=true", headers=auth)) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    print(f"  count={data['data']['count']}")
