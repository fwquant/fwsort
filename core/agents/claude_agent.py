# Anthropic Claude 智能体适配器
import json
import time

from loguru import logger

from core.agents.base import BaseAgent, PredictionResult
from core.config import settings


class ClaudeAgent(BaseAgent):
    """Claude 3.5/4 智能体"""

    name = "Claude"
    model = settings.ANTHROPIC_MODEL

    def __init__(self) -> None:
        self._client = None
        if settings.ANTHROPIC_API_KEY:
            try:
                from anthropic import AsyncAnthropic  # 延迟导入

                self._client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"ClaudeAgent init failed: {exc}")

    async def predict(self, symbol: str, timeframe: str) -> PredictionResult:
        t0 = time.perf_counter()
        prompt = self.build_prompt(symbol, timeframe)

        if not self._client:
            return self._mock(symbol, timeframe, t0, reason="no_api_key")

        try:
            resp = await self._client.messages.create(
                model=self.model,
                max_tokens=200,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}],
            )
            content = resp.content[0].text if resp.content else "{}"
            # Claude 可能输出 ```json 包裹，做一次清理
            content_clean = content.strip()
            if content_clean.startswith("```"):
                content_clean = content_clean.strip("`").replace("json", "", 1).strip()
            data = json.loads(content_clean)
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
            logger.warning(f"Claude predict failed, fallback MOCK: {exc}")
            return self._mock(symbol, timeframe, t0, reason=str(exc))

    def _mock(self, symbol: str, timeframe: str, t0: float, reason: str) -> PredictionResult:
        import random

        direction = 1 if random.random() < 0.65 else 2
        return PredictionResult(
            agent_name=self.name,
            agent_model=self.model,
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            confidence=round(random.uniform(0.55, 0.95), 4),
            reasoning=f"[MOCK:{reason}] 资金费率与订单簿显示{'多头' if direction == 1 else '空头'}占优",
            raw_payload='{"mock": true}',
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
