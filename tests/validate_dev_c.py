# 开发C模块专项验证：跟单/租用/通知/认证
import json
import sys
import requests

BASE = "http://localhost:8000"


def p(label, data):
    print(f"\n=== {label} ===")
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str)[:1500])


def main():
    s = requests.Session()

    # ============ 1. 用户注册 + 登录（认证模块 C1）============
    r = s.post(f"{BASE}/api/auth/register", json={
        "email": "dev_c_test@fwquant.com", "password": "DevC@2026", "nickname": "开发C"
    })
    print("register:", r.status_code, r.json().get("success"), r.json().get("message"))

    r = s.post(f"{BASE}/api/auth/login", json={
        "email": "dev_c_test@fwquant.com", "password": "DevC@2026"
    })
    data = r.json()
    if not data.get("success"):
        # 用现成的管理员账户
        r = s.post(f"{BASE}/api/auth/login", json={
            "email": "admin@fwquant.com", "password": "admin123456"
        })
        data = r.json()
    token = data["data"]["access_token"]
    user_id = data["data"]["user_id"]
    print("login user_id:", user_id, "token len:", len(token))
    s.headers["Authorization"] = f"Bearer {token}"

    # ============ 2. 拉一个已 seed 的 leader UID ============
    r = s.get(f"{BASE}/api/follow/market?rank_type=all_time&limit=5")
    p("跟单市场（C2）", r.json())
    if not r.json()["success"] or not r.json()["data"]["items"]:
        print("No leader available, abort")
        return
    leader_uid = r.json()["data"]["items"][0]["leader_uid"]
    print(f"\n>>> Using leader: {leader_uid}")

    # ============ 3. 跟单订阅（C2）============
    r = s.post(f"{BASE}/api/follow/subscribe", params={
        "leader_uid": leader_uid, "mode": 3, "amount": 50, "months": 1
    })
    p("跟单订阅（mode=3 订阅+分成）", r.json())
    sub_id = r.json().get("data", {}).get("id")

    # ============ 4. 我的订阅 ============
    r = s.get(f"{BASE}/api/follow/my")
    p("我的订阅列表", r.json())

    # ============ 5. 取消订阅 ============
    if sub_id:
        r = s.delete(f"{BASE}/api/follow/{sub_id}")
        p("取消订阅", r.json())

    # ============ 6. 智能体租用清单（C3 公开）============
    r = s.get(f"{BASE}/api/rental/agents")
    p("可租用智能体列表", r.json())
    agents = r.json().get("data", {}).get("agents", [])
    if not agents:
        print("No rental agents seeded, skipping")
    else:
        first = agents[0]
        aid = first["id"]
        # ============ 7. 按次调用（C3 双轨 1）============
        r = s.post(f"{BASE}/api/rental/call", params={
            "agent_id": aid, "symbol": "BTCUSDT", "timeframe": "15m"
        })
        p(f"按次调用 {first['name']}", r.json())

        # ============ 8. 包时段租用（C3 双轨 2）============
        r = s.post(f"{BASE}/api/rental/rent", params={"agent_id": aid, "hours": 24})
        p(f"包时段 24h 租用", r.json())
        rent_order_id = r.json().get("data", {}).get("id")

        # ============ 9. 我的租用 ============
        r = s.get(f"{BASE}/api/rental/my")
        p("我的租用", r.json())

        # ============ 10. 取消包时段 ============
        if rent_order_id:
            r = s.post(f"{BASE}/api/rental/{rent_order_id}/cancel")
            p("取消包时段", r.json())

    # ============ 11. 通知列表（C7）============
    r = s.get(f"{BASE}/api/notify/list?only_unread=false&limit=20")
    p("通知列表", r.json())

    # ============ 12. 全部已读 ============
    r = s.post(f"{BASE}/api/notify/read-all")
    p("全部已读", r.json())

    # ============ 13. 通知接口二次拉取（应全已读）============
    r = s.get(f"{BASE}/api/notify/list?only_unread=true&limit=20")
    p("未读通知（应为空）", r.json())

    # ============ 14. 跟单订单列表 ============
    if sub_id:
        r = s.get(f"{BASE}/api/follow/orders/{sub_id}")
        p("跟单订单（空）", r.json())

    # ============ 15. JWT refresh ============
    r2 = s.post(f"{BASE}/api/auth/login", json={
        "email": "dev_c_test@fwquant.com", "password": "DevC@2026"
    })
    rt = r2.json()["data"]["refresh_token"]
    r = s.post(f"{BASE}/api/auth/refresh?refresh_token=" + rt)
    p("JWT refresh", r.json())

    # ============ 16. 当前用户信息 ============
    r = s.get(f"{BASE}/api/auth/me")
    p("当前用户 /me", r.json())


if __name__ == "__main__":
    main()
