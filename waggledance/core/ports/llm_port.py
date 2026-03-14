"""LLM generation port."""

from typing import Protocol


class LLMPort(Protocol):
    """Port for LLM text generation."""

    async def generate(
        self,
        prompt: str,
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> str: ...

    async def is_available(self) -> bool: ...

    def get_active_model(self) -> str: ...
