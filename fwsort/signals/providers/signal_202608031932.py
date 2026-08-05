"""HTTP URL 信号源: signal_202608031932

类别: 自定义信号 (custom)
创建时间: 2026-08-03 19:31:46
HTTP URL: https://jsonplaceholder.typicode.com/posts/1

使用说明：
    1. 该信号源会请求配置的 HTTP URL
    2. URL 返回 JSON，需包含: symbol, direction, amount, timestamp
    3. 如果返回格式不符，会使用默认值
    4. 如需自定义解析逻辑，可编辑此文件

参数说明：
    - http_url (str): HTTP 请求地址
    - timeout (int): 请求超时秒数（隐藏参数）
"""
from __future__ import annotations

import json
import time
from urllib.request import urlopen, Request

from fwsort.signals.base import Direction, Signal, SignalProvider


class Signal202608031932Provider(SignalProvider):
    """HTTP URL 信号提供者

    请求 HTTP URL 获取 JSON 信号数据。

    参数：
        - http_url: HTTP 请求地址
    """

    name: str = "signal_202608031932"
    category: str = "custom"
    description: str = "自定义 HTTP URL 信号源"

    # 显示参数（Web 可编辑）
    parameters = ["http_url"]
    # 隐藏参数
    hidden_parameters = ["timeout"]

    # 参数默认值（会被 Web 界面修改）
    http_url: str = "https://jsonplaceholder.typicode.com/posts/1"
    timeout: int = 10

    def __init__(self, config_json: dict | None = None):
        self.config = config_json or {}
        if self.config:
            self.http_url = self.config.get("http_url", self.http_url)
            self.timeout = self.config.get("timeout", self.timeout)

    def get_signal(self) -> Signal:
        """从 HTTP URL 获取信号

        请求配置的 URL，解析 JSON 返回 Signal 对象。
        JSON 格式: {"symbol": "...", "direction": "UP/DOWN", "amount": 1.0, "timestamp": 123456}
        """
        try:
            req = Request(
                self.http_url,
                headers={"User-Agent": "fwsort-signal/1.0"}
            )
            with urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            symbol = data.get("symbol", f"btc-updown-4h-{int(time.time())}")
            direction = data.get("direction", "")
            if direction not in ("UP", "DOWN"):
                direction = ""
            amount = float(data.get("amount", 1.0))
            timestamp = int(data.get("timestamp", int(time.time())))

            return Signal(
                symbol=symbol,
                amount=amount,
                direction=direction,
                source=self.name,
                timestamp=timestamp,
            )
        except Exception as e:
            from loguru import logger
            logger.warning(f"[{self.name}] HTTP request failed: {e}, returning default signal")
            return Signal(
                symbol=f"btc-updown-4h-{int(time.time())}",
                amount=1.0,
                direction="",
                source=self.name,
            )

    def health_check(self) -> dict:
        """健康检查 - 测试 URL 是否可达"""
        try:
            req = Request(
                self.http_url,
                headers={"User-Agent": "fwsort-health-check/1.0"}
            )
            with urlopen(req, timeout=self.timeout) as resp:
                return {
                    "provider": self.name,
                    "category": self.category,
                    "ready": True,
                    "http_url": self.http_url,
                    "status_code": resp.status,
                }
        except Exception as e:
            return {
                "provider": self.name,
                "category": self.category,
                "ready": False,
                "http_url": self.http_url,
                "error": str(e),
            }