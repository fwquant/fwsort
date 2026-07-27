# 执行质量评估模块（架构文档 4.3.5）
from dataclasses import dataclass

from fwsort.config import settings


@dataclass
class ExecutionMetrics:
    """订单执行质量指标"""
    execution_rate: float = 0.0      # 订单执行率 0~1
    avg_slippage: float = 0.0        # 平均滑点 0~1
    avg_latency_ms: int = 0          # 平均执行延迟(ms)
    cancel_rate: float = 0.0         # 撤单率 0~1
    trade_count: int = 0             # 交易笔数


def calculate_execution_score(
    execution_rate: float,
    avg_slippage: float,
    avg_latency_ms: int,
    cancel_rate: float,
    base_latency_ms: int = 500,
) -> float:
    """
    计算执行质量分（满分1.0）
    
    架构文档公式：
    执行质量分 = 0.3×执行率 + 0.3×(1-滑点率) + 0.2×(1-延迟/基准延迟) + 0.2×(1-撤单率)
    
    Args:
        execution_rate: 订单执行率 (0~1)
        avg_slippage: 平均滑点率 (0~1)
        avg_latency_ms: 平均执行延迟(ms)
        cancel_rate: 撤单率 (0~1)
        base_latency_ms: 基准延迟(ms)，默认500ms
    
    Returns:
        执行质量分 (0~1)
    """
    # 归一化处理
    execution_norm = max(0.0, min(execution_rate, 1.0))
    slippage_norm = max(0.0, min(avg_slippage, 1.0))
    
    # 延迟归一化：超过基准延迟的部分按比例扣减
    latency_ratio = min(avg_latency_ms / base_latency_ms, 2.0)  # 最大扣减100%
    latency_norm = max(0.0, 1.0 - latency_ratio * 0.5)
    
    cancel_norm = max(0.0, min(cancel_rate, 1.0))
    
    # 加权计算
    score = (
        0.3 * execution_norm
        + 0.3 * (1.0 - slippage_norm)
        + 0.2 * latency_norm
        + 0.2 * (1.0 - cancel_norm)
    )
    
    return round(max(0.0, min(score, 1.0)), 4)


def calculate_from_logs(order_logs: list[dict]) -> ExecutionMetrics:
    """
    从订单执行日志计算执行质量指标
    
    Args:
        order_logs: 订单执行日志列表，每个日志包含:
            - status: 订单状态 (1-已提交 2-部分成交 3-全部成交 4-已撤销 5-失败)
            - slippage: 滑点率
            - latency_ms: 执行延迟(ms)
    
    Returns:
        ExecutionMetrics 执行质量指标
    """
    if not order_logs:
        return ExecutionMetrics()
    
    total = len(order_logs)
    executed = 0
    cancelled = 0
    failed = 0
    total_slippage = 0.0
    total_latency = 0
    executed_count = 0
    
    for log in order_logs:
        status = log.get("status", 0)
        if status == 3:  # 全部成交
            executed += 1
            executed_count += 1
            total_slippage += log.get("slippage", 0.0)
            total_latency += log.get("latency_ms", 0)
        elif status == 2:  # 部分成交
            executed += 1
            executed_count += 1
            total_slippage += log.get("slippage", 0.0)
            total_latency += log.get("latency_ms", 0)
        elif status == 4:  # 已撤销
            cancelled += 1
        elif status == 5:  # 失败
            failed += 1
    
    return ExecutionMetrics(
        execution_rate=executed / total if total > 0 else 0.0,
        avg_slippage=total_slippage / executed_count if executed_count > 0 else 0.0,
        avg_latency_ms=total_latency // executed_count if executed_count > 0 else 0,
        cancel_rate=cancelled / total if total > 0 else 0.0,
        trade_count=total,
    )


def get_execution_grade(score: float) -> str:
    """
    根据执行质量分获取评级
    
    Args:
        score: 执行质量分 (0~1)
    
    Returns:
        评级字符串
    """
    if score >= 0.9:
        return "S"
    elif score >= 0.8:
        return "A"
    elif score >= 0.7:
        return "B"
    elif score >= 0.6:
        return "C"
    else:
        return "D"