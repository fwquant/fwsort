"""Hermes SFTP 信号提供者

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

from fwsort.signals.base import Direction, Signal, SignalProvider

from fwsort.signals.providers.hermes.去hermes拿信号_demo import (
    get_btc_signal_via_sftp,
    connect_sftp,
    get_signal_sftp,
    close_sftp,
    get_btc_history_via_sftp,
    get_history_sftp,
)


class SftpSignalProvider(SignalProvider):
    """Hermes SFTP 信号提供者

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

    # 显示参数（Web 可编辑）
    parameters = ["host", "username", "amount"]
    # 隐藏参数
    hidden_parameters = ["password", "long_connection"]

    # 参数默认值（会被 Web 界面修改）
    host: str = "100.64.0.9"
    username: str = "khadas"
    password: str = ""
    amount: float = 1.0
    long_connection: bool = False

    def __init__(
            self,
            config_json: dict | None = None,
            host: str | None = None,
            username: str | None = None,
            password: str | None = None,
            amount: float | None = None,
            long_connection: bool | None = None,
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

    def health_check(self) -> dict:
        return {
            "provider": self.name,
            "category": self.category,
            "ready": True,
            "host": self.host,
            "long_connection": self.long_connection,
            "connected": self._connected,
        }

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