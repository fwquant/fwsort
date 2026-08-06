# Google Gemini 智能体适配器
import json
import time

from fwsort.fwlogs import logger

from fwsort.agents.base import BaseAgent, PredictionResult
from fwsort.config import settings


class GeminiAgent(BaseAgent):
    """Gemini 2.0/2.5 多模态智能体"""

    name = "Gemini"
    model = settings.GEMINI_MODEL

    def __init__(self) -> None:
        self._client = None
        if settings.GEMINI_API_KEY:
            try:
                import google.generativeai as genai  # 延迟导入

                genai.configure(api_key=settings.GEMINI_API_KEY)
                self._client = genai.GenerativeModel(self.model)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"GeminiAgent init failed: {exc}")

    async def predict(self, symbol: str, timeframe: str) -> PredictionResult:
        t0 = time.perf_counter()
        prompt = self.build_prompt(symbol, timeframe)

        if not self._client:
            return self._mock(symbol, timeframe, t0, reason="no_api_key")

        try:
            # google-generativeai 同步 API，放到线程池执行
            import asyncio

            def _call():
                return self._client.generate_content(
                    prompt,
                    generation_config={"response_mime_type": "application/json", "temperature": 0.3},
                )

            resp = await asyncio.get_event_loop().run_in_executor(None, _call)
            content = resp.text or "{}"
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
            logger.warning(f"Gemini predict failed, fallback MOCK: {exc}")
            return self._mock(symbol, timeframe, t0, reason=str(exc))

    def _mock(self, symbol: str, timeframe: str, t0: float, reason: str) -> PredictionResult:
        import random

        direction = 1 if random.random() < 0.7 else 2
        return PredictionResult(
            agent_name=self.name,
            agent_model=self.model,
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            confidence=round(random.uniform(0.55, 0.95), 4),
            reasoning=f"[MOCK:{reason}] 多周期共振信号, 倾向{'多' if direction == 1 else '空'}",
            raw_payload='{"mock": true}',
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
