"""HTTP/Webhook 外部信号提供者

预留外部信号接入接口：
    - 支持 HTTP POST 推送信号
    - 支持 Webhook 回调
    - 信号可通过 set_external_signal() 方法手动注入
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from fwsort.signals.base import Signal, SignalProvider


class HttpSignalProvider(SignalProvider):
    """HTTP 外部信号提供者

    通过 HTTP POST /api/signals/http 推送信号到系统。
    """

    name: str = "http"
    category: str = "external"  # 外部信号

    def __init__(self, config_json: dict | None = None):
        self.config = config_json or {}
        self.webhook_url = self.config.get("webhook_url", "")
        self.api_key = self.config.get("api_key", "")
        self._pending_signal: Signal | None = None

    def set_external_signal(self, signal: Signal) -> None:
        """设置外部信号（通过 API 推送时调用）"""
        self._pending_signal = signal
        logger.info(f"[HttpSignalProvider] external signal set: {signal.symbol} dir={signal.direction}")

    def get_signal(self) -> Signal:
        """获取信号

        如果有外部推送的信号则返回外部信号，否则返回一个默认测试信号
        """
        if self._pending_signal is not None:
            signal = self._pending_signal
            self._pending_signal = None
            return signal

        logger.warning("[HttpSignalProvider] no external signal available, returning default")
        return Signal(
            symbol="btc-updown-4h-default",
            amount=1.0,
            direction="UP",
            source=self.name,
        )

    async def push_signal(self, signal_data: dict[str, Any]) -> Signal:
        """接收外部推送的信号（供路由层调用）"""
        try:
            signal = Signal.from_dict(signal_data)
            self.set_external_signal(signal)
            return signal
        except Exception as e:
            logger.error(f"[HttpSignalProvider] failed to parse external signal: {e}")
            raise ValueError(f"Invalid signal data: {e}")

    def health_check(self) -> dict:
        return {
            "provider": self.name,
            "category": self.category,
            "ready": True,
            "webhook_url": self.webhook_url,
            "has_pending_signal": self._pending_signal is not None,
        }