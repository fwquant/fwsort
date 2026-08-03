"""验证登录错误提示"""
import requests
import json

base = 'http://127.0.0.1:8002'

# 错误密码
print('=== 错误密码登录 ===')
r = requests.post(base + '/api/auth/login', json={'email': 'admin@fwquant.com', 'password': 'wrongpassword'}, timeout=10)
print(f'STATUS: {r.status_code}')
data = r.json()
print(f'success={data.get("success")}')
print(f'message={data.get("message")}')

# 正确密码
print('\n=== 正确密码登录 ===')
r = requests.post(base + '/api/auth/login', json={'email': 'admin@fwquant.com', 'password': 'admin123456'}, timeout=10)
print(f'STATUS: {r.status_code}')
data = r.json()
print(f'success={data.get("success")}')
print(f'message={data.get("message")}')
token = data.get('data', {}).get('access_token', '')
print(f'token={token[:40]}...' if token else 'token=(empty)')
