"""D2 综合验证脚本（独立运行：python scripts/_dev/d2_smoke.py）
验证 WP-06 物理隔离 / WP-07 跟单自动执行 / WP-08 权重重算 / WP-09 ES异步化+outbox
"""
import os
import sys
import urllib.request
import urllib.error
import json
import time

BASE = os.environ.get('FWSORT_BASE', 'http://127.0.0.1:8000')


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
    print(f'D2 综合验证  (BASE={BASE})')
    print('=' * 60)

    # 0) 健康检查
    print('\n[0] 健康检查')
    code, body = req('GET', '/api/info')
    print(f'  /api/info:        HTTP {code}  (生产通道)')
    code, body = req('GET', '/api/demo/info')
    print(f'  /api/demo/info:   HTTP {code}  (演示通道)')
    if code == 200:
        d = json.loads(body)
        print(f'  is_demo={d.get("is_demo")} demo_db={d.get("demo_db")}')

    # 1) 生产登录
    print('\n[1] 生产登录 admin@fwquant.com / admin123456')
    code, body = req('POST', '/api/auth/login',
                     {'email': 'admin@fwquant.com', 'password': 'admin123456'})
    print(f'  HTTP {code}')
    if code != 200:
        print(f'  失败: {body[:200]}')
        return
    token = json.loads(body)['data']['access_token']

    # 2) 演示登录
    print('\n[2] 演示登录 demo@fwquant.com / demo123456')
    code, body = req('POST', '/api/demo/auth/login',
                     {'email': 'demo@fwquant.com', 'password': 'demo123456'})
    print(f'  HTTP {code}')
    if code != 200:
        print(f'  失败: {body[:200]}')
        return
    demo_token = json.loads(body)['data']['access_token']

    # 3) WP-06 物理隔离验证
    print('\n[3] WP-06 物理隔离验证')
    code, body = req('GET', '/api/agent/accounts',
                     headers={'Authorization': 'Bearer ' + token})
    prod_uids = [a['uid'] for a in json.loads(body).get('data', {}).get('accounts', [])]
    print(f'  生产账户: {len(prod_uids)} 个 → {prod_uids[:3]}{"..." if len(prod_uids) > 3 else ""}')

    code, body = req('GET', '/api/demo/agent/accounts',
                     headers={'Authorization': 'Bearer ' + demo_token})
    demo_uids = [a['uid'] for a in json.loads(body).get('data', {}).get('accounts', [])]
    print(f'  演示账户: {len(demo_uids)} 个 → {demo_uids[:3]}{"..." if len(demo_uids) > 3 else ""}')

    overlap = set(prod_uids) & set(demo_uids)
    if overlap:
        print(f'  ❌ 隔离失败: 重叠 UID = {overlap}')
    else:
        print('  ✅ 隔离通过（无 UID 重叠）')

    # 4) 演示页面角标
    print('\n[4] 演示页面标识')
    code, body = req('GET', '/')
    has_badge = 'fw-demo-badge' in body
    has_footer = 'fw-prod-footer' in body
    print(f'  /:     badge={has_badge} footer={has_footer} (生产: badge=False footer=True)')

    code, body = req('GET', '/demo')
    has_badge = 'fw-demo-badge' in body
    has_footer = 'fw-prod-footer' in body
    print(f'  /demo: badge={has_badge} footer={has_footer} (演示: badge=True footer=False)')

    # 5) WP-09 predict-and-vote 流程（ES 异步化验证）
    print('\n[5] WP-09 predict-and-vote 流程（ES 异步化 + outbox 兜底）')
    code, body = req('GET', '/api/agent/accounts',
                     headers={'Authorization': 'Bearer ' + token})
    accounts = json.loads(body).get('data', {}).get('accounts', [])
    if not accounts:
        print('  生产无账户，跳过')
    else:
        acc = accounts[0]
        t0 = time.perf_counter()
        code, body = req('POST', f'/api/agent/predict-and-vote?account_id={acc["id"]}',
                         {'symbol': 'BTCUSDT', 'timeframe': '15m'},
                         headers={'Authorization': 'Bearer ' + token})
        elapsed = (time.perf_counter() - t0) * 1000
        print(f'  HTTP {code} (耗时 {elapsed:.0f}ms)')
        try:
            d = json.loads(body)
            if d.get('success'):
                print(f'  vote_id={d["data"].get("vote_id")} direction={d["data"].get("final_direction")} amount={d["data"].get("order_amount_usd")}')
            else:
                print(f'  message: {d.get("message", "")[:150]}')
        except Exception:
            print(f'  body: {body[:200]}')

    # 6) WP-09 outbox 投递触发
    print('\n[6] WP-09 outbox 投递（手动 flush_outbox）')
    code, body = req('POST', '/api/admin/trigger/flush_outbox',
                     headers={'Authorization': 'Bearer ' + token})
    print(f'  HTTP {code}')
    print(f'  {body[:200]}')

    # 7) WP-08 权重重算
    print('\n[7] WP-08 权重重算（修改权重后榜单自动重算）')
    new_w = {
        'weight_annualized': 0.40,
        'weight_drawdown': 0.15,
        'weight_sharpe': 0.20,
        'weight_profit_loss': 0.15,
        'weight_execution': 0.10,
    }
    code, body = req('PUT', '/api/ranking/config/weights?rank_type=4', new_w,
                     headers={'Authorization': 'Bearer ' + token})
    print(f'  PUT /api/ranking/config/weights: HTTP {code}')
    time.sleep(2)
    code, body = req('GET', '/api/ranking/global?rank_type=all_time&page=1&page_size=5',
                     headers={'Authorization': 'Bearer ' + token})
    print(f'  GET /api/ranking/global: HTTP {code}')
    try:
        d = json.loads(body)
        items = d.get('data', {}).get('items', [])
        print(f'  榜单前5: {[(i.get("uid"), i.get("composite_score")) for i in items[:5]]}')
    except Exception:
        print(f'  body: {body[:200]}')

    # 8) WP-07 跟单自动执行（手动触发）
    print('\n[8] WP-07 跟单自动执行任务（手动触发）')
    code, body = req('POST', '/api/agent/tasks/follow_auto_copy/trigger',
                     headers={'Authorization': 'Bearer ' + token})
    print(f'  HTTP {code}')
    print(f'  {body[:200]}')

    # 9) 任务状态查询
    print('\n[9] 任务状态查询')
    code, body = req('GET', '/api/agent/tasks',
                     headers={'Authorization': 'Bearer ' + token})
    print(f'  HTTP {code}')
    if code == 200:
        d = json.loads(body)
        tasks = d.get('data', {}).get('tasks', [])
        for t in tasks:
            print(f"  {t.get('task'):32s} status={t.get('status'):8s} last_run={t.get('last_run_at')}")

    print('\n' + '=' * 60)
    print('D2 综合验证完成')
    print('=' * 60)


if __name__ == '__main__':
    main()
