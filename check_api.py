import urllib.request
import json
with urllib.request.urlopen("http://localhost:8000/api/ranking/list") as resp:
    data = json.loads(resp.read().decode("utf-8"))
    items = data["data"]["items"]
    total = data["data"]["total"]
    print(f"total={total}, items_count={len(items)}")
    print(f"top1: {items[0]['name']} score={items[0]['composite_score']} tier={items[0]['tier']}")
