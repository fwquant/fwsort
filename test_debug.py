import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

async def test_index():
    from main import app
    from starlette.testclient import TestClient
    
    client = TestClient(app)
    print("Testing /health ...")
    try:
        r = client.get("/health", timeout=5)
        print(f"  Status: {r.status_code}, Body: {r.text}")
    except Exception as e:
        print(f"  Error: {type(e).__name__}: {e}")
    
    print("\nTesting /api/info ...")
    try:
        r = client.get("/api/info", timeout=5)
        print(f"  Status: {r.status_code}, Body: {r.text[:200]}")
    except Exception as e:
        print(f"  Error: {type(e).__name__}: {e}")
    
    print("\nTesting / (index page) with 10s timeout ...")
    try:
        r = client.get("/", timeout=10)
        print(f"  Status: {r.status_code}, Content-length: {len(r.content)}")
        print(f"  First 300 chars: {r.text[:300]}")
    except Exception as e:
        print(f"  Error: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(test_index())