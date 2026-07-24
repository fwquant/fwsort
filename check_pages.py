import urllib.request
import json

pages = ["/", "/detail", "/accounts", "/follow", "/rental", "/admin"]
js_files = [
    "/static/js/fwui/theme.js",
    "/static/js/fwui/toast.js",
    "/static/js/fwui/modal.js",
    "/static/js/fwui/api.js",
    "/static/js/fwui/utils.js",
    "/static/js/fwui/charts.js",
    "/static/js/app.js",
    "/static/js/ranking_list.js",
    "/static/js/ranking_detail.js",
    "/static/js/accounts.js",
    "/static/js/follow.js",
    "/static/js/rental.js",
    "/static/css/style.css",
    "/static/css/fwui.css",
    "/static/css/theme_dark.css",
    "/static/css/theme_light.css",
]

print("=== HTML pages ===")
for p in pages:
    try:
        with urllib.request.urlopen(f"http://localhost:8000{p}") as resp:
            body = resp.read()
            ct = resp.headers.get("Content-Type", "")
            print(f"  {p}: status={resp.status} bytes={len(body)} ct={ct}")
    except Exception as e:
        print(f"  {p}: ERROR {e}")

print("=== Static files ===")
for p in js_files:
    try:
        with urllib.request.urlopen(f"http://localhost:8000{p}") as resp:
            body = resp.read()
            ct = resp.headers.get("Content-Type", "")
            print(f"  {p}: status={resp.status} bytes={len(body)} ct={ct}")
    except Exception as e:
        print(f"  {p}: ERROR {e}")

print("=== APIs ===")
apis = [
    ("/api/ranking/list?rank_type=realtime", "GET"),
    ("/api/ranking/list?rank_type=daily", "GET"),
    ("/api/auth/me", "GET"),
    ("/api/follow/my", "GET"),
    ("/api/rental/agents", "GET"),
    ("/api/rental/my", "GET"),
    ("/api/notify/list", "GET"),
    ("/api/info", "GET"),
    ("/health", "GET"),
]
for path, method in apis:
    try:
        req = urllib.request.Request(f"http://localhost:8000{path}", method=method)
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            print(f"  {method} {path}: status={resp.status} bytes={len(body)}")
    except urllib.error.HTTPError as e:
        print(f"  {method} {path}: HTTP {e.code} (likely needs auth, ok)")
    except Exception as e:
        print(f"  {method} {path}: ERROR {e}")
