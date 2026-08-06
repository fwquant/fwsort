"""Hermes SFTP 策略

通过 SFTP 从远程读取 cron 预生成的 btc_signal.json，转换为统一的 Signal 对象。
默认使用 get_btc_signal_via_sftp() 一键获取方式（每次新建连接，用完即关）。

依赖: pip install paramiko

参数说明：
    - host (str): SFTP 主机地址
    - username (str): SFTP 用户名
    - password (str): SFTP 密码（隐藏参数）
    - amount (float): 下单金额
    - long_connection (bool): 是否使用长连接（隐藏参数）
"""
from __future__ import annotations

import time
from typing import Optional

from fwsort.strategy.base import Direction, Signal, StrategyBase

from fwsort.strategy.providers.hermes.去hermes拿信号_demo import (
    get_btc_signal_via_sftp,
    connect_sftp,
    get_signal_sftp,
    close_sftp,
    get_btc_history_via_sftp,
    get_history_sftp,
)


class SftpStrategy(StrategyBase):
    """Hermes SFTP 策略

    通过 SFTP 读取远程 cron 生成的 btc_signal.json 文件。
    支持两种模式：
        - 单次模式（默认）: 每次 get_signal() 新建连接，用完即关
        - 长连接模式: connect() 一次，后续 get_signal() 毫秒级返回

    参数：
        - host: SFTP 主机地址
        - username: SFTP 用户名
        - amount: 下单金额
    """

    name: str = "hermes_sftp"
    category: str = "external"
    description: str = "Hermes SFTP 远程信号源"



    # 参数默认值（会被 Web 界面修改）
    host: str = "100.64.0.9"
    username: str = "khadas"
    password: str = ""
    amount: float = 1.0
    long_connection: bool = False
    # 策略参数
    max_up_price: float = 60.0       # UP 价格上限（百分比 0-100），超过不开仓
    max_cycle_ratio: float = 0.5     # 周期时间比例上限（0-1），超过不开单
    cycle_seconds: int = 900         # 周期秒数（5m=300, 15m=900, 4h=14400）


    # 显示参数（Web 可编辑 其值 ）
    parameters = ["host", "username", "amount", "max_up_price", "max_cycle_ratio", "cycle_seconds"]
    # 隐藏参数
    hidden_parameters = ["password", "long_connection"]

    def __init__(
            self,
            config_json: dict | None = None,
            host: str | None = None,
            username: str | None = None,
            password: str | None = None,
            amount: float | None = None,
            long_connection: bool | None = None,
            max_up_price: float | None = None,
            max_cycle_ratio: float | None = None,
            cycle_seconds: int | None = None,
    ):
        self.config = config_json or {}
        # 兼容直接传参和 config_json 两种方式
        self.host = host or self.config.get("host", self.host)
        self.username = username or self.config.get("username", self.username)
        self.password = password or self.config.get("password", self.password)
        self.amount = amount or self.config.get("amount", self.amount)
        lc = long_connection if long_connection is not None else self.config.get("long_connection",
                                                                                 self.long_connection)
        self.long_connection = bool(lc)
        self.max_up_price = max_up_price if max_up_price is not None else self.config.get("max_up_price", self.max_up_price)
        self.max_cycle_ratio = max_cycle_ratio if max_cycle_ratio is not None else self.config.get("max_cycle_ratio", self.max_cycle_ratio)
        self.cycle_seconds = cycle_seconds if cycle_seconds is not None else self.config.get("cycle_seconds", self.cycle_seconds)
        self._connected = False

    def connect(self) -> bool:
        """建立长连接（可选，用于高频读取场景）"""
        if self.long_connection:
            self._connected = connect_sftp(
                host=self.host,
                username=self.username,
                password=self.password,
            )
            return self._connected
        return True

    def close(self):
        """关闭长连接"""
        if self._connected:
            close_sftp()
            self._connected = False

    # 获取一个信号（==========核心方法==========）
    def get_signal(self) -> Signal:
        """获取一个信号

        Returns:
            Signal: 统一格式的信号对象，direction 为空字符串时表示无有效交易信号
        """
        if self.long_connection and self._connected:
            raw = get_signal_sftp()
        else:
            raw = get_btc_signal_via_sftp()

        if raw is None:
            return Signal(
                symbol="",
                amount=self.amount,
                direction="",
                source=self.name,
                timestamp=int(time.time()),
            )

        raw_direction = raw.get("下单方向", "")
        direction: Direction = raw_direction if raw_direction in ("UP", "DOWN") else ""
        symbol = raw.get("标的代码", "")

        return Signal(
            symbol=symbol,
            amount=self.amount,
            direction=direction,
            source=self.name,
            timestamp=int(time.time()),
        )

    # 健康检查
    def health_check(self) -> dict:
        return {
            "provider": self.name,
            "category": self.category,
            "ready": True,
            "host": self.host,
            "long_connection": self.long_connection,
            "connected": self._connected,
        }

    # 获取历史信号列表
    def get_history(self, limit: int = 100) -> list[dict]:
        """获取历史信号列表

        Args:
            limit: 最多返回条数

        Returns:
            历史信号列表，最新的在前
        """
        if self.long_connection and self._connected:
            return get_history_sftp(limit=limit)
        return get_btc_history_via_sftp(
            host=self.host,
            username=self.username,
            password=self.password,
            limit=limit,
        )

    # 开仓条件判断
    def should_open(self, signal: Signal, ctx: dict) -> tuple[bool, str]:
        """开仓条件判断

        规则：
            1. UP 价格 > max_up_price（百分比）不开仓
            2. 周期时间过半（elapsed/period > max_cycle_ratio）不开单
            3. 行情获取失败保守拒绝

        Args:
            signal: 信号对象
            ctx: 上下文，需包含 "gateway"（pm类实例）和 "now"（datetime）

        Returns:
            (allow, reason)
        """
        import asyncio

        gateway = ctx.get("gateway")
        now = ctx.get("now")

        if gateway is None:
            return False, "网关未初始化，无法获取行情"

        # ===== 规则1：UP 价格检查 =====
        up_price_percent = 0.0
        try:
            loop = asyncio.new_event_loop()
            try:
                prices = loop.run_until_complete(gateway.获得_updown价格(signal.symbol))
            finally:
                loop.close()

            if not prices or "UP" not in prices:
                return False, "行情获取失败：返回数据异常"

            up_info = prices.get("UP", {})
            up_mid_str = up_info.get("midpoint")
            if up_mid_str is None:
                return False, "行情获取失败：UP midpoint 为空"

            up_mid = float(up_mid_str)  # 0-1 范围
            up_price_percent = up_mid * 100  # 转为百分比

            if up_price_percent > self.max_up_price:
                return False, f"UP价格{up_price_percent:.1f}% > {self.max_up_price}%，不开仓"
        except Exception as e:
            return False, f"行情获取失败: {e}"

        # ===== 规则2：周期时间过半检查 =====
        ratio = 0.0
        if now:
            try:
                now_ts = now.timestamp()
                cycle = self.cycle_seconds
                if cycle > 0:
                    epoch_start = (int(now_ts) // cycle) * cycle
                    elapsed = now_ts - epoch_start
                    ratio = elapsed / cycle

                    if ratio > self.max_cycle_ratio:
                        return False, f"周期已过{ratio * 100:.0f}% > {self.max_cycle_ratio * 100:.0f}%，不开单"
            except Exception as e:
                return False, f"周期时间计算失败: {e}"

        return True, f"条件满足(UP={up_price_percent:.1f}%, 周期={ratio * 100:.0f}%)"