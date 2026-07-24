# Hermes MoA（Mixture-of-Agents）聚合器
# 参考：NousResearch Hermes MoA
# 思路：第 1 层 3 智能体并行预测 → 第 2 层用"裁判智能体"对 3 个结果进行综合 → 输出最终信号
# 简化实现：第 1 层输出 3 候选；第 2 层用"高置信度优先 + 多数票"启发式聚合
import asyncio
import time
from dataclasses import dataclass
from typing import Iterable

from core.agents.base import BaseAgent, PredictionResult


@dataclass
class AggregatedResult:
    """MoA 聚合后的最终信号"""

    final_direction: int
    final_confidence: float
    layer1_results: list[PredictionResult]
    final_reasoning: str


class HermesMoA:
    """Hermes MoA 聚合器（V1.0 简化版：分层聚合 + 多数票 + 置信度加权）"""

    def __init__(self, agents: Iterable[BaseAgent], layers: int = 2) -> None:
        self.agents = list(agents)
        self.layers = layers

    async def aggregate(self, symbol: str, timeframe: str) -> AggregatedResult:
        """分层聚合：第 1 层 3 智能体并行 → 第 2 层 多数票 + 置信度仲裁"""
        t0 = time.perf_counter()

        # 第 1 层：3 智能体并行预测
        layer1 = await asyncio.gather(
            *[a.predict(symbol, timeframe) for a in self.agents],
            return_exceptions=False,
        )

        # 第 2 层：聚合逻辑（多数票 + 置信度加权 + 高置信度优先）
        final_direction, final_confidence, reasoning = self._arbitrate(layer1)

        latency = (time.perf_counter() - t0) * 1000
        reasoning = f"[Hermes MoA {self.layers}层 | {latency:.0f}ms] {reasoning}"

        return AggregatedResult(
            final_direction=final_direction,
            final_confidence=final_confidence,
            layer1_results=layer1,
            final_reasoning=reasoning,
        )

    @staticmethod
    def _arbitrate(results: list[PredictionResult]) -> tuple[int, float, str]:
        """仲裁：先按多数票，平局按最高置信度"""
        up = [r for r in results if r.direction == 1]
        down = [r for r in results if r.direction == 2]
        flat = [r for r in results if r.direction == 0]

        if len(up) >= 2 and len(up) > len(down):
            avg_conf = sum(r.confidence for r in up) / len(up)
            return 1, round(avg_conf, 4), f"{len(up)}/{len(results)} 智能体看涨 (置信度 {avg_conf:.2f})"
        if len(down) >= 2 and len(down) > len(up):
            avg_conf = sum(r.confidence for r in down) / len(down)
            return 2, round(avg_conf, 4), f"{len(down)}/{len(results)} 智能体看跌 (置信度 {avg_conf:.2f})"
        # 平局：取最高置信度的方向
        if results:
            top = max(results, key=lambda r: r.confidence)
            return top.direction, top.confidence, f"平局按最高置信度仲裁 → {['震荡', '看涨', '看跌'][top.direction]}"
        return 0, 0.0, "无有效预测"


# 工厂：构建 3 智能体 + Hermes MoA
def build_hermes_moa() -> HermesMoA:
    from core.agents.claude_agent import ClaudeAgent
    from core.agents.gemini_agent import GeminiAgent
    from core.agents.openai_agent import OpenAIAgent
    from core.config import settings

    agents = [OpenAIAgent(), ClaudeAgent(), GeminiAgent()]
    return HermesMoA(agents, layers=settings.HERMES_MOA_LAYERS)
