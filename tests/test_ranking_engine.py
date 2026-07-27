# 榜单评分引擎单元测试
import pytest

from fwsort.indicator_calculator import (
    TradeRecord,
    cumulative_return,
    max_consecutive_losses,
    volatility,
)
from fwsort.ranking_engine import WeightTuple, composite_score, tier_of


class TestRankingEngine:
    """榜单评分引擎测试类"""

    def test_weight_tuple_total(self):
        """测试：权重元组求和"""
        weights = WeightTuple(0.25, 0.25, 0.20, 0.15, 0.15)
        assert weights.total() == 1.0

    def test_composite_score_perfect(self):
        """测试：完美指标得分"""
        score = composite_score(
            annualized=2.0,      # 最大化
            max_drawdown=0.0,    # 最小化
            sharpe=3.0,          # 较高
            profit_loss=3.0,     # 最大化
            execution_score=1.0, # 完美
        )
        assert score >= 90
        assert tier_of(score) == "钻石"

    def test_composite_score_good(self):
        """测试：良好指标得分"""
        score = composite_score(
            annualized=0.5,
            max_drawdown=0.15,
            sharpe=1.5,
            profit_loss=1.5,
            execution_score=0.85,
        )
        assert 40 <= score <= 80
        # 根据权重配置，得分可能在黄金到铂金之间
        assert tier_of(score) in ["黄金", "铂金"]

    def test_composite_score_average(self):
        """测试：中等指标得分"""
        score = composite_score(
            annualized=0.2,
            max_drawdown=0.25,
            sharpe=0.8,
            profit_loss=1.0,
            execution_score=0.70,
        )
        assert 40 <= score < 60
        assert tier_of(score) == "黄金"

    def test_composite_score_poor(self):
        """测试：较差指标得分"""
        score = composite_score(
            annualized=-0.1,
            max_drawdown=0.4,
            sharpe=-0.5,
            profit_loss=0.5,
            execution_score=0.50,
        )
        assert score < 40
        assert tier_of(score) in ["白银", "青铜"]

    def test_composite_score_custom_weights(self):
        """测试：自定义权重"""
        custom_weights = WeightTuple(0.4, 0.3, 0.1, 0.1, 0.1)
        score = composite_score(
            annualized=1.0,
            max_drawdown=0.2,
            sharpe=1.0,
            profit_loss=1.0,
            execution_score=0.8,
            weights=custom_weights,
        )
        assert 0 <= score <= 100

    def test_composite_score_extreme_values(self):
        """测试：极端值截断"""
        # 极端高值应该被截断
        score_high = composite_score(
            annualized=10.0,      # 远超过2.0上限
            max_drawdown=-0.1,    # 负数
            sharpe=10.0,          # 远超过上限
            profit_loss=10.0,     # 远超过3.0上限
            execution_score=2.0,  # 超过1.0上限
        )
        assert 0 <= score_high <= 100

        # 极端低值
        score_low = composite_score(
            annualized=-10.0,     # 负数收益
            max_drawdown=2.0,     # 超过1.0
            sharpe=-10.0,         # 负数夏普
            profit_loss=-1.0,     # 负数盈亏比
            execution_score=-1.0, # 负数执行分
        )
        assert 0 <= score_low <= 100

    def test_tier_diamond(self):
        """测试：钻石段位"""
        assert tier_of(80) == "钻石"
        assert tier_of(95) == "钻石"
        assert tier_of(100) == "钻石"

    def test_tier_platinum(self):
        """测试：铂金段位"""
        assert tier_of(60) == "铂金"
        assert tier_of(79) == "铂金"

    def test_tier_gold(self):
        """测试：黄金段位"""
        assert tier_of(40) == "黄金"
        assert tier_of(59) == "黄金"

    def test_tier_silver(self):
        """测试：白银段位"""
        assert tier_of(20) == "白银"
        assert tier_of(39) == "白银"

    def test_tier_bronze(self):
        """测试：青铜段位"""
        assert tier_of(0) == "青铜"
        assert tier_of(19) == "青铜"


class TestIndicatorCalculator:
    """绩效指标计算器测试类（开发计划B §2.1 B1 收益/风险类指标）"""

    def test_cumulative_return_positive(self):
        """测试：累计收益（盈利）"""
        assert cumulative_return(1000, 1500) == pytest.approx(0.5)
        assert cumulative_return(100, 110) == pytest.approx(0.1)

    def test_cumulative_return_negative(self):
        """测试：累计收益（亏损）"""
        assert cumulative_return(1000, 800) == pytest.approx(-0.2)

    def test_cumulative_return_zero_initial(self):
        """测试：初始资金为 0 时返回 0（保护）"""
        assert cumulative_return(0, 100) == 0.0

    def test_volatility_zero(self):
        """测试：恒定收益波动率为 0"""
        assert volatility([0.01, 0.01, 0.01, 0.01]) == 0.0

    def test_volatility_short(self):
        """测试：序列过短返回 0（保护）"""
        assert volatility([]) == 0.0
        assert volatility([0.01]) == 0.0

    def test_volatility_annualized(self):
        """测试：年化波动率为日波动 * sqrt(252)"""
        daily = [0.01, -0.02, 0.015, -0.005, 0.01]
        v_daily = volatility(daily, annualize=False)
        v_ann = volatility(daily, annualize=True)
        assert v_ann == pytest.approx(v_daily * (252 ** 0.5))

    def test_max_consecutive_losses_all_loss(self):
        """测试：全部亏损时连续次数等于总笔数"""
        trades = [TradeRecord(pnl=-1, opened_at=0, closed_at=1, is_win=False) for _ in range(5)]
        assert max_consecutive_losses(trades) == 5

    def test_max_consecutive_losses_mixed(self):
        """测试：混合时取最大连续亏损段"""
        # W L L L W L L W W
        flags = [True, False, False, False, True, False, False, True, True]
        trades = [TradeRecord(pnl=1, opened_at=0, closed_at=1, is_win=w) for w in flags]
        assert max_consecutive_losses(trades) == 3

    def test_max_consecutive_losses_empty(self):
        """测试：空列表返回 0"""
        assert max_consecutive_losses([]) == 0

    def test_max_consecutive_losses_no_loss(self):
        """测试：全部盈利时返回 0"""
        trades = [TradeRecord(pnl=1, opened_at=0, closed_at=1, is_win=True) for _ in range(3)]
        assert max_consecutive_losses(trades) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])