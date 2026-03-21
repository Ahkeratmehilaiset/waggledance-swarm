"""Stub LLM adapter for testing and --stub mode."""
# implements LLMPort

import datetime

CANNED_RESPONSES = {
    "time": "The current time is {time}.",
    "weather": "Weather data unavailable in stub mode.",
    "default": "Stub response: I received your query about '{query}'.",
}


class StubLLMAdapter:
    """Fast in-process stub for --stub mode and tests."""

    async def generate(
        self,
        prompt: str,
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> str:
        lower = prompt.lower()
        if any(w in lower for w in ("time", "kello", "aika")):
            now = datetime.datetime.now().strftime("%H:%M:%S")
            return CANNED_RESPONSES["time"].format(time=now)
        if any(w in lower for w in ("weather", "sää")):
            return CANNED_RESPONSES["weather"]
        short = prompt[:80].replace("\n", " ")
        return CANNED_RESPONSES["default"].format(query=short)

    async def is_available(self) -> bool:
        return True

    def get_active_model(self) -> str:
        return "stub-v1"
