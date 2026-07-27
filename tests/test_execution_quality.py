# 执行质量评估单元测试
import pytest

from fwsort.execution_quality import (
    ExecutionMetrics,
    calculate_execution_score,
    calculate_from_logs,
    get_execution_grade,
)


class TestExecutionQuality:
    """执行质量评估测试类"""

    def test_calculate_execution_score_perfect(self):
        """测试：完美执行质量"""
        score = calculate_execution_score(
            execution_rate=1.0,
            avg_slippage=0.0,
            avg_latency_ms=0,
            cancel_rate=0.0,
        )
        assert score == 1.0
        assert get_execution_grade(score) == "S"

    def test_calculate_execution_score_good(self):
        """测试：良好执行质量"""
        score = calculate_execution_score(
            execution_rate=0.95,
            avg_slippage=0.005,
            avg_latency_ms=200,
            cancel_rate=0.02,
        )
        assert 0.8 <= score <= 1.0
        # 高分可能达到S级
        assert get_execution_grade(score) in ["A", "S"]

    def test_calculate_execution_score_average(self):
        """测试：中等执行质量"""
        score = calculate_execution_score(
            execution_rate=0.85,
            avg_slippage=0.01,
            avg_latency_ms=400,
            cancel_rate=0.05,
        )
        assert 0.7 <= score <= 0.9
        assert get_execution_grade(score) in ["A", "B"]

    def test_calculate_execution_score_poor(self):
        """测试：较差执行质量"""
        score = calculate_execution_score(
            execution_rate=0.7,
            avg_slippage=0.03,
            avg_latency_ms=800,
            cancel_rate=0.15,
        )
        assert 0.6 <= score <= 0.8
        assert get_execution_grade(score) in ["B", "C"]

    def test_calculate_execution_score_bad(self):
        """测试：差执行质量"""
        score = calculate_execution_score(
            execution_rate=0.5,
            avg_slippage=0.05,
            avg_latency_ms=1500,
            cancel_rate=0.3,
        )
        assert score < 0.6
        assert get_execution_grade(score) == "D"

    def test_calculate_from_logs_empty(self):
        """测试：空日志列表"""
        metrics = calculate_from_logs([])
        assert metrics.execution_rate == 0.0
        assert metrics.avg_slippage == 0.0
        assert metrics.avg_latency_ms == 0
        assert metrics.cancel_rate == 0.0
        assert metrics.trade_count == 0

    def test_calculate_from_logs_normal(self):
        """测试：正常日志数据"""
        logs = [
            {"status": 3, "slippage": 0.002, "latency_ms": 150},  # 全部成交
            {"status": 3, "slippage": 0.005, "latency_ms": 200},  # 全部成交
            {"status": 2, "slippage": 0.003, "latency_ms": 180},  # 部分成交
            {"status": 4, "slippage": 0.0, "latency_ms": 50},     # 已撤销
            {"status": 5, "slippage": 0.0, "latency_ms": 100},    # 失败
        ]
        
        metrics = calculate_from_logs(logs)
        
        assert metrics.trade_count == 5
        assert metrics.execution_rate == 3 / 5  # 3笔成交（2全部+1部分）
        assert metrics.cancel_rate == 1 / 5     # 1笔撤销
        assert metrics.avg_slippage == (0.002 + 0.005 + 0.003) / 3
        assert metrics.avg_latency_ms == (150 + 200 + 180) // 3

    def test_calculate_from_logs_all_cancelled(self):
        """测试：全部撤单"""
        logs = [
            {"status": 4, "slippage": 0.0, "latency_ms": 50},
            {"status": 4, "slippage": 0.0, "latency_ms": 60},
            {"status": 4, "slippage": 0.0, "latency_ms": 40},
        ]
        
        metrics = calculate_from_logs(logs)
        
        assert metrics.trade_count == 3
        assert metrics.execution_rate == 0.0
        assert metrics.cancel_rate == 1.0
        assert metrics.avg_slippage == 0.0
        assert metrics.avg_latency_ms == 0

    def test_execution_metrics_default(self):
        """测试：默认指标值"""
        metrics = ExecutionMetrics()
        assert metrics.execution_rate == 0.0
        assert metrics.avg_slippage == 0.0
        assert metrics.avg_latency_ms == 0
        assert metrics.cancel_rate == 0.0
        assert metrics.trade_count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])