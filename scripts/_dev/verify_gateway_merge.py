# -*- coding: utf-8 -*-
"""
PM 网关整合验证脚本（静态校验：导入/继承/别名/单例/工厂）
执行：python scripts/_dev/verify_gateway_merge.py
"""
import os
import sys

# 让脚本能直接从项目根目录运行
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

# 让 .env 不影响（避免无 key 时被 settings 报错）
os.environ.setdefault("APP_ENV", "development")


def check(name, cond, detail=""):
    flag = "[OK]" if cond else "[FAIL]"
    print(f"  {flag} {name}{(' — ' + detail) if detail else ''}")
    return cond


def main():
    print("=" * 70)
    print("PM 网关整合验证（导入/继承/别名/单例/工厂）")
    print("=" * 70)
    ok = True

    # ---- 1) 顶层包导入（最关键：__init__.py 不能有坏 import） ----
    print("\n[1] 顶层包导入")
    try:
        from fwsort import gateway as gw_pkg
        ok &= check("import fwsort.gateway", True)
    except Exception as e:  # noqa: BLE001
        ok &= check("import fwsort.gateway", False, f"{type(e).__name__}: {e}")
        print("  → 顶层导入失败，后续验证无法继续")
        return False

    # ---- 2) gateway/ 目录文件清单（是否符合"1基类+1统一接口+1平台1网关"） ----
    print("\n[2] gateway/ 目录文件清单")
    gw_dir = os.path.join(ROOT, "fwsort", "gateway")
    files = sorted(
        f for f in os.listdir(gw_dir)
        if f.endswith(".py") and not f.startswith("__")
    )
    print(f"  files = {files}")
    expected = {"base.py", "gateway.py", "okx_gateway.py",
                "polymarket_gateway.py", "simulator_gateway.py"}
    ok &= check("文件清单符合 1基类+1统一接口+1平台3网关", set(files) == expected,
                f"got={files}")

    # ---- 3) 5 个网关类继承 BaseGateway ----
    print("\n[3] 5 个网关类继承 BaseGateway")
    from fwsort.gateway.base import BaseGateway
    for cls_name, cls_path in [
        ("PolymarketGateway", "fwsort.gateway.polymarket_gateway"),
        ("PolymarketV1Client", "fwsort.gateway.polymarket_gateway"),
        ("OkxGateway", "fwsort.gateway.okx_gateway"),
        ("SimulatorGateway", "fwsort.gateway.simulator_gateway"),
        ("ExecutionGateway", "fwsort.gateway.gateway"),
    ]:
        mod = __import__(cls_path, fromlist=[cls_name])
        cls = getattr(mod, cls_name)
        ok &= check(
            f"{cls_name} 继承 BaseGateway",
            issubclass(cls, BaseGateway),
            f"bases={[b.__name__ for b in cls.__mro__[:3]]}"
        )
        inst = cls() if cls_name in ("SimulatorGateway", "ExecutionGateway") else cls.__new__(cls)
        ok &= check(
            f"{cls_name}.name 非空且非 'base'",
            hasattr(inst, "name") and inst.name not in ("", "base"),
            f"name={getattr(inst, 'name', '?')}"
        )
        ok &= check(
            f"{cls_name}.is_ready 可调用",
            callable(getattr(inst, "is_ready", None))
        )

    # ---- 4) 旧类名别名指向新类（向后兼容） ----
    print("\n[4] 旧类名别名（向后兼容）")
    from fwsort.gateway import (
        PolymarketClient, PolymarketV1Client,
        OkxExecutor, OkxGateway,
        OrderSimulator, SimulatorGateway,
    )
    ok &= check("PolymarketClient is PolymarketV1Client",
                PolymarketClient is PolymarketV1Client)
    ok &= check("OkxExecutor is OkxGateway", OkxExecutor is OkxGateway)
    ok &= check("OrderSimulator is SimulatorGateway",
                OrderSimulator is SimulatorGateway)

    # ---- 5) GatewayHub 单例与懒加载 ----
    print("\n[5] GatewayHub 单例")
    from fwsort.gateway import get_hub
    h1 = get_hub()
    h2 = get_hub()
    ok &= check("get_hub() 返回单例", h1 is h2)
    ok &= check("hub.execution is ExecutionGateway",
                isinstance(h1.execution, ExecutionGateway := __import__(
                    "fwsort.gateway", fromlist=["ExecutionGateway"]
                ).ExecutionGateway))
    ok &= check("hub.okx is OkxGateway", isinstance(h1.okx, OkxGateway))
    ok &= check("hub.polymarket_v2 is PolymarketGateway",
                isinstance(h1.polymarket_v2,
                           __import__("fwsort.gateway", fromlist=["PolymarketGateway"])
                           .PolymarketGateway))
    ok &= check("hub.polymarket_v1 is PolymarketV1Client",
                isinstance(h1.polymarket_v1, PolymarketV1Client))
    ok &= check("hub.simulator is SimulatorGateway",
                isinstance(h1.simulator, SimulatorGateway))

    # ---- 6) 兼容旧工厂函数 ----
    print("\n[6] 兼容旧工厂函数")
    from fwsort.gateway import (
        get_polymarket_client, get_polymarket_gateway, get_gateway,
    )
    c1 = get_polymarket_client()
    g1 = get_polymarket_gateway()
    eg = get_gateway()
    ok &= check("get_polymarket_client() = hub.polymarket_v1",
                c1 is h1.polymarket_v1)
    ok &= check("get_polymarket_gateway() = hub.polymarket_v2",
                g1 is h1.polymarket_v2)
    ok &= check("get_gateway() = hub.execution", eg is h1.execution)

    # ---- 7) 模拟盘 submit 异步工作（无需密钥） ----
    print("\n[7] 模拟盘 submit（无需密钥）")
    import asyncio
    async def sim_test():
        sim = h1.simulator
        ok_sim = sim.is_ready()
        ord1 = await sim.submit(platform="polymarket", symbol="BTC-UP",
                               side=1, amount_usd=5.0)
        ord2 = await sim.submit(platform="okx", symbol="BTCUSDT",
                               side=2, amount_usd=10.0)
        return ok_sim, ord1, ord2
    ok_ready, ord1, ord2 = asyncio.run(sim_test())
    ok &= check("simulator.is_ready = True", ok_ready)
    ok &= check("simulator.submit polymarket → order_id 非空", bool(ord1.order_id),
                f"oid={ord1.order_id[:18]}...")
    ok &= check("simulator.submit polymarket status in {2,3}",
                ord1.status in (2, 3), f"status={ord1.status}")
    ok &= check("simulator.submit okx → qty>0 且 price>0",
                ord2.quantity > 0 and ord2.actual_price > 0,
                f"qty={ord2.quantity} px={ord2.actual_price:.2f}")

    # ---- 8) ExecutionGateway 路由（live→降级→simulator） ----
    print("\n[8] ExecutionGateway 路由（含降级）")
    async def route_test():
        eg = h1.execution
        # 无密钥走实盘 → 异常 → 降级 simulator
        r1 = await eg.submit(account_type=1, platform="polymarket",
                             symbol="BTC-UP", side=1, amount_usd=5.0)
        r2 = await eg.submit(account_type=1, platform="okx",
                             symbol="BTCUSDT", side=1, amount_usd=5.0)
        # 显式 account_type=0 → simulator
        r3 = await eg.submit(account_type=0, platform="okx",
                             symbol="BTCUSDT", side=1, amount_usd=5.0)
        return r1, r2, r3
    r1, r2, r3 = asyncio.run(route_test())
    ok &= check("live polymarket → 降级 simulator (is_live=False)",
                r1.is_live is False and bool(r1.order_id))
    ok &= check("live okx → 降级 simulator (is_live=False)",
                r2.is_live is False and bool(r2.order_id))
    ok &= check("simulator 路由 status in {2,3}",
                r3.status in (2, 3), f"status={r3.status}")

    # ---- 9) 生命周期：connect/close 异步 OK ----
    print("\n[9] 生命周期 connect/close")
    async def lifecycle_test():
        sim = SimulatorGateway()
        await sim.connect()
        opened = sim._http is not None and not sim._http.is_closed
        await sim.close()
        closed = sim._http is None or sim._http.is_closed
        return opened, closed
    opened, closed = asyncio.run(lifecycle_test())
    ok &= check("connect 后 _http 开启", opened)
    ok &= check("close 后 _http 关闭", closed)

    # ---- 10) __all__ 完整性 ----
    print("\n[10] __init__.py __all__ 完整性")
    all_exports = set(gw_pkg.__all__)
    needed = {
        "BaseGateway", "PolymarketGateway", "PolymarketV1Client",
        "PolymarketClient", "OkxGateway", "OkxExecutor",
        "SimulatorGateway", "OrderSimulator",
        "ExecutionGateway", "GatewayHub",
        "get_hub", "get_gateway",
        "get_polymarket_client", "get_polymarket_gateway",
    }
    missing = needed - all_exports
    ok &= check("__all__ 覆盖所有关键符号", not missing,
                f"missing={missing}" if missing else f"count={len(all_exports)}")

    print("\n" + "=" * 70)
    print(f"  RESULT: {'✅ ALL PASS' if ok else '❌ SOME FAILED'}")
    print("=" * 70)
    return ok


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
