"""Unit tests for StubLLMAdapter — canned responses for stub mode."""

import pytest

from waggledance.adapters.llm.stub_llm_adapter import StubLLMAdapter


class TestStubLLMAdapter:
    """StubLLMAdapter contract tests."""

    @pytest.mark.asyncio
    async def test_is_available_returns_true(self, stub_llm: StubLLMAdapter) -> None:
        assert await stub_llm.is_available() is True

    @pytest.mark.asyncio
    async def test_generate_returns_non_empty_string(self, stub_llm: StubLLMAdapter) -> None:
        result = await stub_llm.generate("Hello, world!")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_active_model_returns_stub_v1(self, stub_llm: StubLLMAdapter) -> None:
        assert stub_llm.get_active_model() == "stub-v1"

    @pytest.mark.asyncio
    async def test_time_related_prompt_returns_time_info(self, stub_llm: StubLLMAdapter) -> None:
        result = await stub_llm.generate("What time is it?")
        assert "time" in result.lower()

    @pytest.mark.asyncio
    async def test_does_not_raise_on_empty_prompt(self, stub_llm: StubLLMAdapter) -> None:
        result = await stub_llm.generate("")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_weather_prompt_returns_weather_response(self, stub_llm: StubLLMAdapter) -> None:
        result = await stub_llm.generate("What is the weather like?")
        assert "weather" in result.lower()

    @pytest.mark.asyncio
    async def test_finnish_time_word_triggers_time_response(self, stub_llm: StubLLMAdapter) -> None:
        result = await stub_llm.generate("Paljonko kello on?")
        assert "time" in result.lower()

    @pytest.mark.asyncio
    async def test_default_response_includes_query_excerpt(self, stub_llm: StubLLMAdapter) -> None:
        result = await stub_llm.generate("Tell me about beekeeping")
        assert "beekeeping" in result.lower()
