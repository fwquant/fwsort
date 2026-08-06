# 信号生成器：random / gpt-4o / claude / gemini / moa
# 无 API Key 时一律降级为 random（与 README "无 key 时降级 MOCK" 一致）
from __future__ import annotations

import random

from fwsort.fwlogs import logger

# 信号枚举：UP=1, DOWN=2, NEUTRAL=0
UP, DOWN, NEUTRAL = "UP", "DOWN", "NEUTRAL"

_VALID_SOURCES = ("random", "gpt-4o", "claude", "gemini", "moa")


def generate_signal(source: str = "random", symbol: str | None = None) -> str:
    """根据 source 生成一次信号；无 API Key 降级 random
    返回值：'UP' / 'DOWN' / 'NEUTRAL'
    """
    from fwsort.config import settings

    source = (source or "random").lower()
    if source not in _VALID_SOURCES:
        source = "random"

    # moa 走聚合（无 key 时也是 random）
    if source == "moa":
        # 简易 MoA：3 票随机，按 2:1 多数
        votes = [random.choice([UP, DOWN, NEUTRAL]) for _ in range(3)]
        if votes.count(UP) >= 2:
            return UP
        if votes.count(DOWN) >= 2:
            return DOWN
        return NEUTRAL

    # 单智能体：仅当有对应 key 时尝试调用；无 key 直接 random
    if source == "gpt-4o" and settings.OPENAI_API_KEY:
        # 真实调用留作扩展点；当前 MVP 仍走 random
        logger.debug("openai key present, but MVP falls back to random")
    if source == "claude" and settings.ANTHROPIC_API_KEY:
        logger.debug("anthropic key present, but MVP falls back to random")
    if source == "gemini" and settings.GEMINI_API_KEY:
        logger.debug("gemini key present, but MVP falls back to random")

    return random.choice([UP, DOWN, NEUTRAL])


def signal_to_direction(signal: str) -> int:
    """把信号字符串映射为下单方向：1=多 2=空 0=无"""
    if signal == UP:
        return 1
    if signal == DOWN:
        return 2
    return 0
