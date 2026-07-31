# -*- coding: utf-8 -*-
"""验证 target_epoch 精确查询功能"""
import asyncio
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
os.environ.setdefault("APP_ENV", "development")


async def test():
    from fwsort.gateway import get_hub
    gw = get_hub().polymarket_v2

    # 1) 默认查询（当前最活跃）
    r1 = await gw.get_active_btc5m_market()
    s1 = r1.get("success")
    c1 = r1.get("count")
    m1 = r1.get("msg", "")
    print(f"[默认] success={s1} count={c1} msg={m1}")

    # 2) 精确查询（你贴的 URL 中的时间戳）
    r2 = await gw.get_active_btc5m_market(target_epoch=1785399000)
    s2 = r2.get("success")
    c2 = r2.get("count")
    m2 = r2.get("msg", "")
    print(f"[精确] success={s2} count={c2} msg={m2}")
    if r2.get("success"):
        data = r2.get("data")
        if isinstance(data, list) and data:
            print(f"  slug={data[0].get('slug', '?')}")
            print(f"  question={data[0].get('question', '?')[:80]}")
        elif isinstance(data, dict):
            print(f"  slug={data.get('slug', '?')}")

    # 3) 查一个未来 5min 窗口（当前时间 + 5min 对齐）
    import time
    now = int(time.time())
    future_epoch = (now // 300 + 2) * 300  # 2 个窗口后的 5min 对齐
    r3 = await gw.get_active_btc5m_market(target_epoch=future_epoch)
    s3 = r3.get("success")
    c3 = r3.get("count")
    m3 = r3.get("msg", "")
    print(f"[未来] target_epoch={future_epoch} success={s3} count={c3} msg={m3}")

    await gw.close()


if __name__ == "__main__":
    asyncio.run(test())
