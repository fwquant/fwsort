# 投票引擎单元测试（V1.0 规则）
import pytest

from fwsort.config import settings
from fwsort.voting import Direction, vote


class TestVotingEngine:
    """投票引擎测试类"""

    def test_vote_all_up(self):
        """测试：三个智能体全部看涨 → 加倍订单"""
        result = vote(
            directions=[Direction.UP, Direction.UP, Direction.UP],
            account_balance=1000.0,
            daily_pnl=100.0,
            initial_balance=1000.0,
        )
        assert result.final_direction == Direction.UP
        assert result.order_amount_usd == settings.ORDER_DOUBLE_USD
        assert result.reason == "double_10"

    def test_vote_all_down(self):
        """测试：三个智能体全部看跌 → 加倍订单"""
        result = vote(
            directions=[Direction.DOWN, Direction.DOWN, Direction.DOWN],
            account_balance=1000.0,
            daily_pnl=100.0,
            initial_balance=1000.0,
        )
        assert result.final_direction == Direction.DOWN
        assert result.order_amount_usd == settings.ORDER_DOUBLE_USD
        assert result.reason == "double_10"

    def test_vote_majority_up(self):
        """测试：2:1 多数看涨 → 基础订单"""
        result = vote(
            directions=[Direction.UP, Direction.UP, Direction.DOWN],
            account_balance=1000.0,
            daily_pnl=100.0,
            initial_balance=1000.0,
        )
        assert result.final_direction == Direction.UP
        assert result.order_amount_usd == settings.ORDER_BASE_USD
        assert "base_5" in result.reason

    def test_vote_majority_down(self):
        """测试：2:1 多数看跌 → 基础订单"""
        result = vote(
            directions=[Direction.DOWN, Direction.DOWN, Direction.UP],
            account_balance=1000.0,
            daily_pnl=100.0,
            initial_balance=1000.0,
        )
        assert result.final_direction == Direction.DOWN
        assert result.order_amount_usd == settings.ORDER_BASE_USD
        assert "base_5" in result.reason

    def test_vote_no_consensus(self):
        """测试：无共识（1涨1跌1平）→ 不交易"""
        result = vote(
            directions=[Direction.UP, Direction.DOWN, Direction.FLAT],
            account_balance=1000.0,
            daily_pnl=100.0,
            initial_balance=1000.0,
        )
        assert result.final_direction == Direction.FLAT
        assert result.order_amount_usd == 0.0
        assert result.reason == "no_consensus"

    def test_vote_risk_freeze(self):
        """测试：日亏超过30% → 风控冻结"""
        result = vote(
            directions=[Direction.UP, Direction.UP, Direction.UP],
            account_balance=700.0,
            daily_pnl=-300.0,  # 日亏300，初始1000，亏损30%
            initial_balance=1000.0,
        )
        assert result.final_direction == Direction.FLAT
        assert result.order_amount_usd == 0.0
        assert "risk_freeze" in result.reason

    def test_vote_single_limit(self):
        """测试：单笔金额超过余额20% → 限额"""
        result = vote(
            directions=[Direction.UP, Direction.UP, Direction.UP],
            account_balance=40.0,  # 20% = 8美元，小于加倍金额10美元
            daily_pnl=0.0,
            initial_balance=100.0,
        )
        assert result.final_direction == Direction.UP
        assert result.order_amount_usd == 8.0  # 40 * 0.2 = 8
        assert "capped_by_risk" in result.reason

    def test_vote_counts(self):
        """测试：投票计数正确性"""
        result = vote(
            directions=[Direction.UP, Direction.UP, Direction.DOWN],
            account_balance=1000.0,
            daily_pnl=0.0,
            initial_balance=1000.0,
        )
        assert result.up_count == 2
        assert result.down_count == 1
        assert result.flat_count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])