# -*- coding: utf-8 -*-
"""
跨调用点 import 回归（不依赖网络/数据库）
验证 router/scheduler/tests 中的旧 import 仍能通过兼容层解析
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
os.environ.setdefault("APP_ENV", "development")


def check(name, fn):
    try:
        fn()
        print(f"  [OK] {name}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] {name}: {type(e).__name__}: {e},traceback={traceback.format_exc()}")
        return False


def check_eq(name, actual, expected):
    if actual == expected:
        print(f"  [OK] {name}")
        return True
    print(f"  [FAIL] {name}: actual={actual!r} expected={expected!r}")
    return False


def main():
    print("=" * 70)
    print(" 跨调用点 import 回归（router/scheduler/tests）")
    print("=" * 70)
    ok = True

    # ========== 1) fwsort.execution.* 兼容 ==========
    print("\n[1] fwsort.execution.* 兼容（迁移到 order_log + gateway.simulator_gateway）")
    ok &= check("from fwsort.execution.simulator import OrderSimulator",
                lambda: __import__("fwsort.execution.simulator", fromlist=["OrderSimulator"]).OrderSimulator)
    ok &= check("from fwsort.execution.es_writer import schedule_index_order_log",
                lambda: __import__("fwsort.execution.es_writer", fromlist=["schedule_index_order_log"]).schedule_index_order_log)
    ok &= check("from fwsort.execution.es_writer import search_order_logs",
                lambda: __import__("fwsort.execution.es_writer", fromlist=["search_order_logs"]).search_order_logs)
    ok &= check("from fwsort.execution.outbox import build_order_log_event",
                lambda: __import__("fwsort.execution.outbox", fromlist=["build_order_log_event"]).build_order_log_event)
    ok &= check("from fwsort.execution.outbox import flush_outbox_sync",
                lambda: __import__("fwsort.execution.outbox", fromlist=["flush_outbox_sync"]).flush_outbox_sync)
    ok &= check("from fwsort.execution.outbox import dispatch_event",
                lambda: __import__("fwsort.execution.outbox", fromlist=["dispatch_event"]).dispatch_event)
    ok &= check("from fwsort.execution.outbox import mark_event_failure, OUTBOX_MAX_RETRY",
                lambda: __import__("fwsort.execution.outbox", fromlist=["mark_event_failure", "OUTBOX_MAX_RETRY"]))

    # ========== 2) fwsort.gateway.<old_name> 兼容 ==========
    print("\n[2] fwsort.gateway.{polymarket_client, okx_client, okx_executor} 兼容")
    ok &= check("from fwsort.gateway.polymarket_client import PolymarketClient",
                lambda: __import__("fwsort.gateway.polymarket_client", fromlist=["PolymarketClient"]).PolymarketClient)
    ok &= check("from fwsort.gateway.okx_client import OkxClient",
                lambda: __import__("fwsort.gateway.okx_client", fromlist=["OkxClient"]).OkxClient)
    ok &= check("from fwsort.gateway.okx_executor import OkxExecutor",
                lambda: __import__("fwsort.gateway.okx_executor", fromlist=["OkxExecutor"]).OkxExecutor)
    ok &= check("from fwsort.gateway.gateway import get_gateway",
                lambda: __import__("fwsort.gateway.gateway", fromlist=["get_gateway"]).get_gateway)

    # ========== 3) 关键调用点（router/admin_router / agent_router / polymarket_router）==========
    print("\n[3] 路由层关键调用点")
    ok &= check("import fwsort.execution.simulator (admin_router 用)",
                lambda: __import__("fwsort.execution.simulator", fromlist=["OrderSimulator"]))
    ok &= check("import fwsort.execution.es_writer (agent_router 用)",
                lambda: __import__("fwsort.execution.es_writer", fromlist=["schedule_index_order_log", "search_order_logs"]))
    ok &= check("import fwsort.execution.outbox (agent_router/scheduler 用)",
                lambda: __import__("fwsort.execution.outbox", fromlist=["build_order_log_event", "flush_outbox_sync"]))
    ok &= check("import fwsort.gateway.polymarket_client (polymarket_router 用)",
                lambda: __import__("fwsort.gateway.polymarket_client", fromlist=["PolymarketClient"]))

    # ========== 4) 旧 import 拿到的类与新类是同一个对象 ==========
    print("\n[4] 旧 import 拿到的类与新类是同一个对象（身份等价）")
    from fwsort.gateway.simulator_gateway import OrderSimulator as NewOrderSimulator
    from fwsort.gateway.okx_gateway import OkxExecutor as NewOkxExecutor
    from fwsort.gateway.polymarket.polymarket_gateway import PolymarketClient as NewPolymarketClient
    from fwsort.execution.simulator import OrderSimulator as OldOS
    from fwsort.execution.es_writer import schedule_index_order_log as OldSchedule
    from fwsort.gateway.okx_executor import OkxExecutor as OldOE
    from fwsort.gateway.polymarket_client import PolymarketClient as OldPC
    ok &= check_eq("OrderSimulator 同一对象", OldOS is NewOrderSimulator, True)
    ok &= check_eq("OkxExecutor 同一对象", OldOE is NewOkxExecutor, True)
    ok &= check_eq("PolymarketClient 同一对象", OldPC is NewPolymarketClient, True)
    from fwsort.order_log.es_writer import schedule_index_order_log as NewSchedule
    ok &= check_eq("schedule_index_order_log 同一函数", OldSchedule is NewSchedule, True)

    # ========== 5) 验证完整模块列表：gateway/ 严格符合 1+1+3 规范 ==========
    print("\n[5] gateway/ 目录严格符合 1基类+1统一接口+1平台3网关")
    gw_dir = os.path.join(ROOT, "fwsort", "gateway")
    files = sorted(f for f in os.listdir(gw_dir) if f.endswith(".py"))
    print(f"  files = {files}")
    expected = {"__init__.py", "base.py", "gateway.py", "okx_gateway.py",
                "polymarket_gateway.py", "simulator_gateway.py"}
    ok &= check_eq("目录无任何 .py 残留", set(files), expected)

    # ========== 6) execution/ 目录确认已删除 ==========
    print("\n[6] execution/ 目录已删除（功能已迁出）")
    exec_dir = os.path.join(ROOT, "fwsort", "execution")
    if not os.path.exists(exec_dir):
        print("  [OK] fwsort/execution/ 目录不存在")
    else:
        print(f"  [FAIL] fwsort/execution/ 仍存在 → {os.listdir(exec_dir)}")
        ok = False

    print("\n" + "=" * 70)
    print(f"  RESULT: {'✅ ALL PASS' if ok else '❌ SOME FAILED'}")
    print("=" * 70)
    return ok


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
