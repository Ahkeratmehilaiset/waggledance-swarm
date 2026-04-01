"""Ollama LLM adapter with circuit breaker protection."""
# implements LLMPort

import time
import logging
import asyncio

import httpx

logger = logging.getLogger(__name__)

OPEN_THRESHOLD = 3
RECOVERY_SECONDS = 30


class OllamaAdapter:
    """Ollama HTTP adapter with circuit breaker and retry logic."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        default_model: str = "phi4-mini",
        timeout_seconds: float = 120.0,
        max_retries: int = 2,
        num_gpu: int | None = None,
    ):
        self._base_url = base_url
        self._active_model = default_model
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._num_gpu = num_gpu
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
        )
        self._circuit_failures = 0
        self._circuit_open_since: float | None = None
        self._circuit_total_trips = 0

    async def generate(
        self,
        prompt: str,
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> str:
        """Generate with circuit breaker protection."""
        if self._is_circuit_open():
            logger.warning("LLM call blocked by circuit breaker (model=%s)", self._active_model)
            return ""

        use_model = self._active_model if model == "default" else model
        payload: dict = {
            "model": use_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if self._num_gpu is not None:
            payload["options"]["num_gpu"] = self._num_gpu

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = await self._client.post("/api/generate", json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data.get("response", "").strip()

                if not content:
                    logger.warning("Ollama returned empty response (model=%s, attempt=%d)", use_model, attempt + 1)
                    if attempt < self._max_retries - 1:
                        await asyncio.sleep(1)
                        continue
                    self._record_failure()
                    return ""

                self._record_success()
                return content

            except httpx.ConnectError as e:
                logger.warning("Ollama unavailable: %s", type(e).__name__)
                self._record_failure()
                return ""

            except (httpx.ReadTimeout, httpx.WriteTimeout) as e:
                last_error = e
                logger.warning("Ollama timeout (attempt %d/%d): %s", attempt + 1, self._max_retries, type(e).__name__)
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2)
                    continue

            except httpx.HTTPStatusError as e:
                logger.error("Ollama HTTP error: %d %s", e.response.status_code, e)
                last_error = e
                if e.response.status_code == 503 and attempt < self._max_retries - 1:
                    await asyncio.sleep(3)
                    continue
                break

            except Exception as e:
                logger.error("LLM error: %s: %s (model=%s)", type(e).__name__, e or "(empty)", use_model)
                last_error = e
                break

        self._record_failure()
        return ""

    async def is_available(self) -> bool:
        """HEAD /api/tags -- fast health check."""
        try:
            resp = await self._client.get("/api/tags")
            return resp.status_code == 200
        except Exception:
            return False

    def get_active_model(self) -> str:
        return self._active_model

    @property
    def is_degraded(self) -> bool:
        """True when the circuit breaker is open (LLM unavailable)."""
        return self._circuit_open_since is not None

    def _is_circuit_open(self) -> bool:
        if self._circuit_open_since is None:
            return False
        if time.monotonic() - self._circuit_open_since > RECOVERY_SECONDS:
            self._circuit_failures = 0
            self._circuit_open_since = None
            logger.info("LLM circuit breaker CLOSED (recovered)")
            return False
        return True

    def _record_success(self) -> None:
        self._circuit_failures = 0
        if self._circuit_open_since is not None:
            self._circuit_open_since = None
            logger.info("LLM circuit breaker CLOSED (recovered)")

    def _record_failure(self) -> None:
        self._circuit_failures += 1
        if self._circuit_failures >= OPEN_THRESHOLD:
            self._circuit_open_since = time.monotonic()
            self._circuit_total_trips += 1
            logger.warning("LLM circuit breaker OPEN (trip #%d)", self._circuit_total_trips)

    async def close(self) -> None:
        """Close the httpx client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
