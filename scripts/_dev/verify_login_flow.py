"""验证登录 + 初始化流程"""
import requests
import json

base = 'http://127.0.0.1:8002'

# 1. 未登录调用 init-all（应返回 401）
print('=== 测试1：未登录调用 init-all（应返回 401）===')
r = requests.post(base + '/api/admin/init-all?n_accounts=20&n_votes=50', timeout=15)
print(f'STATUS: {r.status_code}')
print(f'BODY: {json.dumps(r.json(), ensure_ascii=False, indent=2)}')

# 2. 登录
print('\n=== 测试2：登录 admin@fwquant.com / admin123456 ===')
r = requests.post(base + '/api/auth/login', json={'email': 'admin@fwquant.com', 'password': 'admin123456'}, timeout=15)
print(f'STATUS: {r.status_code}')
data = r.json()
token = data.get('data', {}).get('access_token', '')
msg = data.get('message', '')
succ = data.get('success')
print(f'success={succ} message={msg}')
print(f'TOKEN: {token[:40]}...' if token else 'TOKEN: (empty)')

# 3. 登录后调用 init-all
if token:
    print('\n=== 测试3：登录后调用 init-all ===')
    r = requests.post(
        base + '/api/admin/init-all?n_accounts=20&n_votes=50',
        headers={'Authorization': f'Bearer {token}'},
        timeout=60,
    )
    print(f'STATUS: {r.status_code}')
    body = r.json()
    print(f'BODY: {json.dumps(body, ensure_ascii=False, indent=2)[:800]}')

# 4. has-admin 公开接口
print('\n=== 测试4：has-admin 公开接口 ===')
r = requests.get(base + '/api/auth/has-admin', timeout=10)
print(f'STATUS: {r.status_code}')
print(f'BODY: {json.dumps(r.json(), ensure_ascii=False, indent=2)}')

# 5. /me 验证 token
if token:
    print('\n=== 测试5：/me 验证 token ===')
    r = requests.get(base + '/api/auth/me', headers={'Authorization': f'Bearer {token}'}, timeout=10)
    print(f'STATUS: {r.status_code}')
    print(f'BODY: {json.dumps(r.json(), ensure_ascii=False, indent=2)[:500]}')
