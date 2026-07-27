# 多智能体协调器（架构文档 4.3.4）
from dataclasses import dataclass
from enum import IntEnum
from typing import List

import numpy as np


class CollaborationMode(IntEnum):
    """协作模式枚举"""
    PARALLEL = 1      # 并行执行
    SERIAL = 2        # 串行接力
    CONDITIONAL = 3   # 条件触发
    DYNAMIC = 4       # 动态分配


@dataclass
class AgentContribution:
    """单个智能体的贡献统计"""
    agent_uid: str
    signal_count: int
    win_count: int
    avg_confidence: float
    contribution_score: float


@dataclass
class PortfolioMetrics:
    """策略组合绩效指标"""
    portfolio_id: str
    portfolio_name: str
    collaboration_mode: CollaborationMode
    strategy_uids: List[str]
    total_return: float
    avg_strategy_return: float
    synergy_contribution: float  # 协同收益贡献
    correlation_score: float     # 策略间分散度得分
    resource_utilization: float  # 资源利用率
    conflict_resolution_efficiency: float  # 冲突解决效率
    composite_score: float       # 组合综合得分


def calculate_synergy_contribution(
    portfolio_return: float,
    individual_returns: List[float],
) -> float:
    """
    计算协同收益贡献
    
    协同收益贡献 = 组合收益 - 各策略独立收益之和的平均值
    
    Args:
        portfolio_return: 组合总收益
        individual_returns: 各策略独立收益列表
    
    Returns:
        协同收益贡献值
    """
    if not individual_returns:
        return 0.0
    avg_individual = sum(individual_returns) / len(individual_returns)
    return portfolio_return - avg_individual


def calculate_correlation_diversity(return_series: List[List[float]]) -> float:
    """
    计算策略间相关性（分散度）
    
    Args:
        return_series: 各策略收益序列列表，每个元素是一个收益序列
    
    Returns:
        分散度得分 (0~1)，越高表示分散度越好
    """
    if len(return_series) < 2:
        return 1.0
    
    # 计算两两相关系数
    n = len(return_series)
    correlations = []
    
    for i in range(n):
        for j in range(i + 1, n):
            if len(return_series[i]) > 1 and len(return_series[j]) > 1:
                corr = np.corrcoef(return_series[i], return_series[j])[0, 1]
                correlations.append(abs(corr))
    
    if not correlations:
        return 1.0
    
    # 平均相关系数越低，分散度越好
    avg_corr = sum(correlations) / len(correlations)
    diversity_score = 1.0 - avg_corr
    
    return round(max(0.0, min(diversity_score, 1.0)), 4)


def calculate_portfolio_score(
    avg_strategy_score: float,
    synergy_contribution: float,
    diversity_score: float,
    alpha: float = 0.5,
    beta: float = 0.3,
    gamma: float = 0.2,
) -> float:
    """
    计算组合综合得分（架构文档公式）
    
    组合得分 = α×平均策略得分 + β×协同收益贡献 + γ×分散度得分
    
    Args:
        avg_strategy_score: 各策略平均得分 (0~100)
        synergy_contribution: 协同收益贡献 (归一化后 0~1)
        diversity_score: 分散度得分 (0~1)
        alpha: 平均策略得分权重
        beta: 协同收益贡献权重
        gamma: 分散度得分权重
    
    Returns:
        组合综合得分 (0~100)
    """
    # 归一化协同贡献到 0~1
    synergy_norm = max(0.0, min(synergy_contribution + 0.5, 1.0))
    
    score = (
        alpha * (avg_strategy_score / 100)
        + beta * synergy_norm
        + gamma * diversity_score
    )
    
    return round(min(max(score * 100, 0), 100), 2)


def get_portfolio_tier(score: float) -> str:
    """
    获取组合段位（架构文档 6.3）
    
    Args:
        score: 组合综合得分
    
    Returns:
        段位名称
    """
    if score >= 60:
        return "传奇组合"
    elif score >= 30:
        return "精英组合"
    else:
        return "普通组合"


def get_portfolio_tier_icon(tier: str) -> str:
    """
    获取组合段位图标
    
    Args:
        tier: 段位名称
    
    Returns:
        图标表情符号
    """
    icons = {
        "传奇组合": "🔮",
        "精英组合": "🟢",
        "普通组合": "🟡",
    }
    return icons.get(tier, "🟡")


def detect_conflicts(
    predictions: List[dict],
    threshold: float = 0.7,
) -> List[tuple]:
    """
    检测智能体间信号冲突
    
    Args:
        predictions: 各智能体预测结果列表，每个包含 direction 和 confidence
        threshold: 置信度阈值，超过此值的相反信号视为冲突
    
    Returns:
        冲突对列表，每个元素是 (agent1_uid, agent2_uid)
    """
    conflicts = []
    n = len(predictions)
    
    for i in range(n):
        for j in range(i + 1, n):
            p1 = predictions[i]
            p2 = predictions[j]
            
            # 方向相反且置信度都超过阈值
            if (p1["direction"] != p2["direction"] and
                p1["confidence"] >= threshold and
                p2["confidence"] >= threshold):
                conflicts.append((p1["agent_uid"], p2["agent_uid"]))
    
    return conflicts


def resolve_conflict(
    conflict_pair: tuple,
    predictions: List[dict],
) -> dict:
    """
    解决智能体间冲突（V1.0 简单规则：置信度加权投票）
    
    Args:
        conflict_pair: 冲突的两个智能体UID
        predictions: 所有智能体预测结果
    
    Returns:
        解决后的综合预测
    """
    agent1, agent2 = conflict_pair
    p1 = next((p for p in predictions if p["agent_uid"] == agent1), None)
    p2 = next((p for p in predictions if p["agent_uid"] == agent2), None)
    
    if not p1 or not p2:
        return {"direction": 0, "confidence": 0.0}
    
    # 置信度加权投票
    total_confidence = p1["confidence"] + p2["confidence"]
    if p1["confidence"] > p2["confidence"]:
        direction = p1["direction"]
        confidence = p1["confidence"] * 0.8  # 冲突后置信度降低
    else:
        direction = p2["direction"]
        confidence = p2["confidence"] * 0.8
    
    return {
        "direction": direction,
        "confidence": round(confidence, 4),
        "resolved_from": [agent1, agent2],
    }