"""尝试通过 /events 或 /markets 端点获取 btc-updown 事件的所有市场"""
import asyncio
import json
import time
import httpx


async def explore():
    async with httpx.AsyncClient(timeout=15) as client:
        # 方法 1: /events 不加 slug 过滤，按 title 过滤或翻页
        print("=== 方法 1: /events?q=btc ===")
        try:
            r = await client.get(
                "https://gamma-api.polymarket.com/events",
                params={"q": "btc-updown", "limit": 10},
            )
            data = r.json()
            events = data.get("events", data.get("data", [])) if isinstance(data, dict) else data
            print(f"status={r.status_code}, events={len(events)}")
            for e in events:
                print(f"  title={e.get('title')}, slug={e.get('slug')}, markets={len(e.get('markets', []))}")
        except Exception as e:
            print(f"error: {e}")

        # 方法 2: /events 不加 slug，用 limit+offset 翻页
        print("\n=== 方法 2: /events 翻页 limit=50 ===")
        try:
            r = await client.get(
                "https://gamma-api.polymarket.com/events",
                params={"limit": 50, "offset": 0},
            )
            data = r.json()
            events = data.get("events", data.get("data", [])) if isinstance(data, dict) else data
            print(f"status={r.status_code}, events={len(events)}")
            for e in events:
                slug = e.get("slug", "")
                if "btc" in slug.lower() or "bitcoin" in (e.get("title") or "").lower():
                    print(f"  ★ title={e.get('title')}, slug={slug}")
                    print(f"    markets={len(e.get('markets', []))}")
        except Exception as e:
            print(f"error: {e}")

        # 方法 3: /markets?q=btc-updown
        print("\n=== 方法 3: /markets?q=btc-updown ===")
        try:
            r = await client.get(
                "https://gamma-api.polymarket.com/markets",
                params={"q": "btc-updown", "limit": 10},
            )
            data = r.json()
            print(f"status={r.status_code}, type={type(data).__name__}")
            if isinstance(data, list):
                for m in data[:5]:
                    print(f"  slug={m.get('slug')}, closed={m.get('closed')}, outcomePrices={m.get('outcomePrices')}")
            else:
                print(f"  body={json.dumps(data, ensure_ascii=False)[:500]}")
        except Exception as e:
            print(f"error: {e}")

        # 方法 4: 用 /events 并在 slug 里用前缀匹配
        print("\n=== 方法 4: /events?slug_prefix=btc-updown ===")
        # Gamma API 没有 prefix 参数。试试用 slug=btc-updown 看看会不会支持前缀匹配
        # 之前试过了，空的。再试试 /events 端点的完整 slug
        try:
            # 用完整 slug 查询 (含 epoch)
            epoch_15m = int(time.time()) // 900 * 900
            full_slug = f"btc-updown-15m-{epoch_15m}"
            r = await client.get(
                "https://gamma-api.polymarket.com/events",
                params={"slug": full_slug},
            )
            print(f"events?slug={full_slug} status={r.status_code}")
            data = r.json()
            print(f"  body len: {len(r.text)}, body: {r.text[:300]}")
        except Exception as e:
            print(f"error: {e}")


asyncio.run(explore())
