"""D1+D2 综合验证脚本"""
import urllib.request
import urllib.error
import json
import time

BASE = 'http://localhost:8000'


def req(method, path, data=None, headers=None):
    body = json.dumps(data or {}).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, method=method)
    if data is not None:
        r.add_header('Content-Type', 'application/json')
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        resp = urllib.request.urlopen(r, timeout=10)
        return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main():
    print('=' * 60)
    print('阶段 D1+D2 综合验证')
    print('=' * 60)
    print()

    # 1) admin 登录
    code, body = req('POST', '/api/auth/login',
                     {'email': 'admin@fwquant.com', 'password': 'admin123456'})
    print(f'[1] admin 登录: HTTP {code}')
    if code != 200:
        print(f'    失败: {body[:200]}')
        return
    token = json.loads(body)['data']['access_token']
    print(f'    token 长度: {len(token)}')
    print()

    # 2) WP-09 predict-and-vote（ES 不可用，主流程不阻塞）
    print('[2] WP-09 predict-and-vote 流程（ES 不可用但主流程应不阻塞）')
    # 拿账户
    code, body = req('GET', '/api/agent/accounts', headers={'Authorization': 'Bearer ' + token})
    if code != 200:
        print(f'    accounts: {code} {body[:200]}')
    else:
        accounts = json.loads(body).get('data', {}).get('accounts', [])
        print(f'    当前账户数: {len(accounts)}')
        if accounts:
            acc = accounts[0]
            acc_id = acc.get('id')
            acc_uid = acc.get('uid', 'unknown')
            print(f'    用账户: uid={acc_uid} id={acc_id}')
            t0 = time.perf_counter()
            code, body = req('POST', f'/api/agent/predict-and-vote?account_id={acc_id}',
                             {'symbol': 'BTCUSDT', 'timeframe': '15m'},
                             headers={'Authorization': 'Bearer ' + token})
            elapsed = (time.perf_counter() - t0) * 1000
            print(f'    predict-and-vote: HTTP {code} (耗时 {elapsed:.0f}ms)')
            # 提取关键字段
            try:
                d = json.loads(body)
                if d.get('success'):
                    print(f'    vote_id: {d.get("data", {}).get("vote_id", "?")}')
                    print(f'    final_direction: {d.get("data", {}).get("final_direction", "?")}')
                    print(f'    reason: {d.get("data", {}).get("reason", "?")}')
                else:
                    print(f'    message: {d.get("message", "")[:200]}')
            except Exception as e:
                print(f'    body: {body[:200]}')
    print()

    # 3) WP-06 演示模式独立登录
    print('[3] WP-06 演示模式独立登录（demo@fwquant.com）')
    code, body = req('POST', '/api/demo/auth/login',
                     {'email': 'demo@fwquant.com', 'password': 'demo123456'})
    print(f'    /api/demo/auth/login: HTTP {code}')
    demo_token = None
    if code == 200:
        demo_token = json.loads(body)['data']['access_token']
        code, body = req('GET', '/api/demo/agent/accounts',
                         headers={'Authorization': 'Bearer ' + demo_token})
        accounts = json.loads(body).get('data', {}).get('accounts', [])
        print(f'    demo 账户数: {len(accounts)}')
        if accounts:
            first = accounts[0]
            print(f'    第一个: uid={first.get("uid")} name={first.get("name")}')
    print()

    # 4) WP-06 隔离验证
    print('[4] WP-06 隔离验证：演示 vs 生产 账户列表')
    code, body = req('GET', '/api/agent/accounts', headers={'Authorization': 'Bearer ' + token})
    prod_accounts = json.loads(body).get('data', {}).get('accounts', [])
    prod_uids = [a['uid'] for a in prod_accounts]
    print(f'    生产账户 UID: {prod_uids[:5]}{"..." if len(prod_uids) > 5 else ""} (共{len(prod_uids)}个)')

    if demo_token:
        code, body = req('GET', '/api/demo/agent/accounts',
                         headers={'Authorization': 'Bearer ' + demo_token})
        demo_accounts = json.loads(body).get('data', {}).get('accounts', [])
        demo_uids = [a['uid'] for a in demo_accounts]
        print(f'    演示账户 UID: {demo_uids[:5]}{"..." if len(demo_uids) > 5 else ""} (共{len(demo_uids)}个)')

        overlap = set(prod_uids) & set(demo_uids)
        if overlap:
            print(f'    ⚠️ 隔离失败: 重叠 UID = {overlap}')
        else:
            print('    ✅ 数据隔离 OK（无 UID 重叠）')
    print()

    # 5) WP-06 演示页面 badge 验证
    print('[5] WP-06 演示页面 badge 验证')
    code, body = req('GET', '/')
    has_badge = 'fw-demo-badge' in body
    has_footer = 'fw-prod-footer' in body
    print(f'    /: badge={has_badge} footer={has_footer} (生产模式应: badge=False footer=True)')

    code, body = req('GET', '/demo')
    has_badge = 'fw-demo-badge' in body
    has_footer = 'fw-prod-footer' in body
    print(f'    /demo: badge={has_badge} footer={has_footer} (演示模式应: badge=True footer=False)')
    print()

    # 6) WP-03 限流验证（用不同 email 避免污染之前的）
    print('[6] WP-03 限流验证（同 IP 错误登录触发）')
    for i in range(6):
        code, body = req('POST', '/api/auth/login',
                         {'email': 'attacker@evil.com', 'password': 'wrong_' + str(i)})
        try:
            msg = json.loads(body).get('message', '')
        except Exception:
            msg = body[:80]
        locked = '锁定' in msg or '失败次数过多' in msg
        print(f'    尝试{i + 1}: HTTP {code} {"限流" if locked else ""} {msg[:80]}')
    print()

    # 7) WP-04 鉴权验证
    print('[7] WP-04 鉴权验证（admin 端点）')
    code, body = req('POST', '/api/admin/init-db')
    print(f'    无 token init-db: HTTP {code} {body[:150]}')
    code, body = req('POST', '/api/admin/seed-mock?n_accounts=2&n_votes=2',
                     headers={'Authorization': 'Bearer ' + token})
    print(f'    有 admin token seed-mock: HTTP {code} {body[:150]}')
    print()

    print('=' * 60)
    print('D1+D2 综合验证完成')
    print('=' * 60)


if __name__ == '__main__':
    main()
