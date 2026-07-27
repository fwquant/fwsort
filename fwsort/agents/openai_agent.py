# OpenAI GPT-4o 智能体适配器
import json
import time

from loguru import logger

from fwsort.agents.base import BaseAgent, PredictionResult
from fwsort.config import settings


class OpenAIAgent(BaseAgent):
    """GPT-4o / GPT-4V 多模态智能体"""

    name = "GPT-4o"
    model = settings.OPENAI_MODEL

    def __init__(self) -> None:
        self._client = None
        if settings.OPENAI_API_KEY:
            try:
                from openai import AsyncOpenAI  # 延迟导入，避免无 key 时启动失败

                self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"OpenAIAgent init failed: {exc}")

    async def predict(self, symbol: str, timeframe: str) -> PredictionResult:
        t0 = time.perf_counter()
        prompt = self.build_prompt(symbol, timeframe)

        if not self._client:
            # 无 API key → 兜底 MOCK（README 第15条）
            return self._mock(symbol, timeframe, t0, reason="no_api_key")

        try:
            resp = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是顶级量化交易员，只输出指定 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=200,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or "{}"
            data = json.loads(content)
            return PredictionResult(
                agent_name=self.name,
                agent_model=self.model,
                symbol=symbol,
                timeframe=timeframe,
                direction=int(data.get("direction", 0)),
                confidence=float(data.get("confidence", 0.5)),
                reasoning=str(data.get("reason", ""))[:200],
                raw_payload=content,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"OpenAI predict failed, fallback MOCK: {exc}")
            return self._mock(symbol, timeframe, t0, reason=str(exc))

    def _mock(self, symbol: str, timeframe: str, t0: float, reason: str) -> PredictionResult:
        import random

        await_dummy = random.uniform(0.1, 0.3)
        # 真实异步睡眠：避免阻塞事件循环
        import asyncio

        # 注意：此方法应被 await 调用方在事件循环内；这里通过 time.sleep 不可，
        # 故由调用方负责延迟。简化为直接返回 0 延迟。
        direction = 1 if random.random() < 0.7 else 2
        return PredictionResult(
            agent_name=self.name,
            agent_model=self.model,
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            confidence=round(random.uniform(0.55, 0.95), 4),
            reasoning=f"[MOCK:{reason}] 基于K线+情绪, 短期看{'涨' if direction == 1 else '跌'}概率较高",
            raw_payload='{"mock": true}',
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
