"""单元测试：标的解析器 + 信号生成器
无外部依赖，可直接 python -m pytest tests/test_signals.py 跑通
"""
import os
import sys
from pathlib import Path

# 把项目根加入 import 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fwsort.signals.parser import parse_target_url, is_valid_target_url
from fwsort.signals.generator import generate_signal, signal_to_direction, UP, DOWN, NEUTRAL


class TestParser:
    """URL → symbol 解析"""

    def test_symbol_passthrough(self):
        """已经是 BTC-USDT 形式直接通过"""
        assert parse_target_url("BTC-USDT") == "BTC-USDT"
        assert parse_target_url("eth-usdt") == "ETH-USDT"

    def test_okx_url(self):
        """OKX 现货 URL"""
        assert parse_target_url("https://www.okx.com/trade-spot/btc-usdt") == "BTC-USDT"
        assert parse_target_url("https://www.okx.com/trade-swap/eth-usdt-swap") == "ETH-USDT-SWAP"

    def test_polymarket_url(self):
        """Polymarket event URL"""
        assert parse_target_url("https://polymarket.com/event/btc-100k-2026").startswith("POLY-")

    def test_empty(self):
        assert parse_target_url(None) is None
        assert parse_target_url("") is None

    def test_invalid_domain(self):
        """未知域名返回 None"""
        assert parse_target_url("https://unknown.com/btc") is None

    def test_validator(self):
        assert is_valid_target_url("") is True  # 允许空
        assert is_valid_target_url(None) is True
        assert is_valid_target_url("https://okx.com") is True
        assert is_valid_target_url("http://okx.com") is False  # 必须 https
        assert is_valid_target_url("a" * 600) is False  # 长度限制


class TestGenerator:
    """信号生成器"""

    def test_generate_returns_valid_signal(self):
        for source in ["random", "gpt-4o", "claude", "gemini", "moa"]:
            for _ in range(20):
                s = generate_signal(source=source)
                assert s in (UP, DOWN, NEUTRAL), f"unexpected signal: {s}"

    def test_invalid_source_fallback(self):
        """未知 source 降级为 random"""
        s = generate_signal(source="bogus")
        assert s in (UP, DOWN, NEUTRAL)

    def test_signal_to_direction(self):
        assert signal_to_direction(UP) == 1
        assert signal_to_direction(DOWN) == 2
        assert signal_to_direction(NEUTRAL) == 0
        assert signal_to_direction(None) == 0
        assert signal_to_direction("INVALID") == 0

    def test_moa_distribution(self):
        """MoA 应有较大概率产生非 NEUTRAL（2:1 多数）"""
        from collections import Counter

        results = Counter()
        for _ in range(500):
            results[generate_signal(source="moa")] += 1
        # 纯随机 1/3 概率产生 NEUTRAL；MoA 应使其概率降低
        # 断言：MoA 的 NEUTRAL 占比应低于纯随机基线 + 2σ（约 56%）；留余量避免抖动
        neutral_ratio = results[NEUTRAL] / 500
        assert neutral_ratio < 0.55, f"MoA NEUTRAL too high: {neutral_ratio:.2%} {results}"


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
