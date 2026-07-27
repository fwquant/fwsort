# 智能体基类：统一接口
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PredictionResult:
    """单个智能体一次预测的标准化输出"""

    agent_name: str
    agent_model: str
    symbol: str
    timeframe: str
    direction: int  # 1-涨 2-跌 0-平
    confidence: float
    reasoning: str
    raw_payload: str
    latency_ms: int


class BaseAgent(ABC):
    """智能体抽象基类"""

    name: str = "BaseAgent"
    model: str = ""

    @abstractmethod
    async def predict(self, symbol: str, timeframe: str) -> PredictionResult:
        """对指定交易对与时窗预测下一根K线方向"""
        raise NotImplementedError

    @staticmethod
    def build_prompt(symbol: str, timeframe: str) -> str:
        """统一构造 prompt（3 智能体共用）"""
        return (
            f"你是加密货币量化交易员。请基于公开可获取的信息（K线形态、成交量、链上数据、"
            f"新闻情绪、宏观环境等），预测 {symbol} 在下一个 {timeframe} 时间窗口的涨跌方向。\n"
            f"严格按以下 JSON 格式输出，不要输出任何额外文字：\n"
            f'{{"direction": 1 or 2 or 0, "confidence": 0.0~1.0, "reason": "≤ 80 字中文简述"}}\n'
            f"direction 含义：1=看涨  2=看跌  0=震荡\n"
            f"confidence 取值 0.5~0.95。"
        )
