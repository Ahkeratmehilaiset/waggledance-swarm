"""
WaggleDance Swarm AI — LLM Provider v0.0.1
============================================
Jani Korpi (Ahkerat Mehiläiset)
Claude 4.6 • v0.0.1 • Built: 2026-02-22 14:37 EET

Ollama HTTP API -yhteys parannetulla virhekäsittelyllä.
KORJAUS K8: Tyhjät virheilmoitukset → logitetaan poikkeuksen tyyppi + viesti.
KORJAUS: Retry-logiikka (max 2 yritystä timeout-tapauksissa).
"""

import httpx
import json
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("waggle.llm")


@dataclass
class LLMResponse:
    content: str
    model: str = ""
    tokens_used: int = 0
    raw: dict = None
    error: bool = False

    def __post_init__(self):
        if self.raw is None:
            self.raw = {}


class LLMProvider:
    """Yhteys Ollama-palvelimeen parannetulla virhekäsittelyllä."""

    def __init__(self, config: dict = None):
        config = config or {}
        self.base_url = config.get("base_url", "http://localhost:11434")
        self.model = config.get("model", "qwen2.5:7b")
        self.timeout = config.get("timeout", 120)
        self.max_retries = config.get("max_retries", 2)
        # ── GPU-ohjaus ────────────────────────────────────
        # num_gpu: 0 = pakota CPU:lle, null = Ollama päättää (oletus: GPU)
        # KRIITTINEN: Ilman tätä Ollama lataa 7b:n GPU:lle
        # → unloadaa 32b:n → mallinvaihto 60-120s
        self.num_gpu = config.get("num_gpu", None)
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout)
            )
        return self._client

    async def generate(self, prompt: str, system: str = "",
                       temperature: float = 0.7,
                       max_tokens: int = 1000) -> LLMResponse:
        """Generoi vastaus Ollamalla. Retry-logiikka timeout-virheille."""
        client = await self._get_client()

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            }
        }
        # ── Pakota CPU/GPU ────────────────────────────────
        # num_gpu: 0 → kaikki layerit CPU:lla
        # num_gpu: 99 → kaikki GPU:lla (oletus)
        # Tämä on AINOA tapa ajaa 7b ja 32b rinnakkain yhdellä GPU:lla
        if self.num_gpu is not None:
            payload["options"]["num_gpu"] = self.num_gpu
        if system:
            payload["system"] = system

        last_error = None
        for attempt in range(self.max_retries):
            try:
                resp = await client.post("/api/generate", json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data.get("response", "").strip()

                # KORJAUS: tyhjä vastaus ei ole onnistunut
                if not content:
                    logger.warning(
                        f"Ollama palautti tyhjän vastauksen "
                        f"(malli={self.model}, yritys={attempt+1})"
                    )
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(1)
                        continue
                    return LLMResponse(
                        content="",
                        model=self.model,
                        error=True,
                    )

                return LLMResponse(
                    content=content,
                    model=data.get("model", self.model),
                    tokens_used=data.get("eval_count", 0),
                    raw=data
                )

            except httpx.ConnectError as e:
                logger.error(f"Ollama ei vastaa: {type(e).__name__}: {e}")
                return LLMResponse(
                    content="",
                    model=self.model,
                    error=True,
                )

            except (httpx.ReadTimeout, httpx.WriteTimeout) as e:
                last_error = e
                logger.warning(
                    f"Ollama timeout (yritys {attempt+1}/{self.max_retries}): "
                    f"{type(e).__name__}"
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2)
                    continue

            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Ollama HTTP-virhe: {e.response.status_code} "
                    f"{type(e).__name__}: {e}"
                )
                last_error = e
                if e.response.status_code == 503 and attempt < self.max_retries - 1:
                    await asyncio.sleep(3)
                    continue
                break

            except Exception as e:
                # KORJAUS K8: logitetaan poikkeuksen tyyppi aina
                error_msg = str(e) or "(tyhjä virheilmoitus)"
                logger.error(
                    f"LLM-virhe: {type(e).__name__}: {error_msg} "
                    f"(malli={self.model})"
                )
                last_error = e
                break

        # Kaikki yritykset epäonnistuivat
        error_detail = f"{type(last_error).__name__}: {last_error}" if last_error else "tuntematon"
        return LLMResponse(
            content="",
            model=self.model,
            error=True,
        )

    async def generate_structured(self, prompt: str,
                                   schema_description: str = "",
                                   system: str = "") -> dict:
        """Generoi JSON-vastaus."""
        sys_prompt = (system or "") + "\nVastaa VAIN JSON-muodossa, ei muuta."
        response = await self.generate(prompt, system=sys_prompt, temperature=0.3)

        if response.error:
            return {"error": "LLM-virhe", "raw": ""}

        text = response.content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
            return {"error": "JSON parse failed", "raw": response.content}

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
