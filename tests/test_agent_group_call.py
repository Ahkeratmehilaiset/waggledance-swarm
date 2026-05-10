import json

import pytest

from core.agent_group_call import AgentGroupCallPipeline, GroupAgentSlot
from core.llm_provider import LLMResponse


class FakeLLM:
    def __init__(self, content):
        self.content = content
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return LLMResponse(content=self.content)


class FakeTranslator:
    def __init__(self):
        self.calls = []

    def _to_en(self, text):
        if text == "Miten varroapunkkia käsitellään?":
            return "How should varroa be treated?"
        return f"EN:{text}"

    def fi_to_en(self, text):
        self.calls.append(("fi_to_en", text))
        return self._to_en(text)

    def en_to_fi(self, text):
        self.calls.append(("en_to_fi", text))
        return f"FI:{text}"

    def batch_fi_to_en(self, texts):
        self.calls.append(("batch_fi_to_en", tuple(texts)))
        return [self._to_en(text) for text in texts]

    def batch_en_to_fi(self, texts):
        self.calls.append(("batch_en_to_fi", tuple(texts)))
        return [f"FI:{text}" for text in texts]


@pytest.mark.asyncio
async def test_finnish_query_is_normalized_translated_and_grouped(monkeypatch):
    monkeypatch.setattr(
        "core.agent_group_call.autocorrect_fi",
        lambda text: "Miten varroapunkkia käsitellään?",
    )
    monkeypatch.setattr(
        "core.agent_group_call.normalize_fi",
        lambda text, sort_words=False: "käsitellä varroa",
    )
    llm = FakeLLM(json.dumps({
        "answers": [
            {
                "agent_id": "beekeeper",
                "answer": "Treat varroa after the last honey harvest.",
                "confidence": 0.9,
            },
            {
                "agent_id": "safety",
                "answer": "Use protective handling and follow label limits.",
                "confidence": 0.8,
            },
        ],
        "synthesis": "Varroa treatment needs timing and safety checks.",
    }))
    translator = FakeTranslator()
    pipeline = AgentGroupCallPipeline(llm, translator=translator)

    result = await pipeline.generate_group(
        "Miten varroapunkkia käsitellään?",
        [
            GroupAgentSlot(
                agent_id="beekeeper",
                name="Beekeeper",
                system_prompt="Finnish beekeeping specialist.",
            ),
            GroupAgentSlot(
                agent_id="safety",
                name="Safety Officer",
                system_prompt="Safety specialist.",
            ),
        ],
        shared_context="apiary context",
    )

    assert result.ok is True
    assert result.input_language == "fi"
    assert result.query_corrected == "Miten varroapunkkia käsitellään?"
    assert result.query_normalized_fi == "käsitellä varroa"
    assert result.query_en == "How should varroa be treated?"
    assert len(llm.calls) == 1
    prompt = llm.calls[0]["prompt"]
    assert "How should varroa be treated?" in prompt
    assert "käsitellä varroa" in prompt
    assert result.output_language == "en"
    assert result.answers[0].answer_display.startswith("Treat varroa")
    assert result.synthesis_display.startswith("Varroa treatment")
    assert ("batch_fi_to_en", ("Miten varroapunkkia käsitellään?",)) in translator.calls
    assert not any(call[0] == "batch_en_to_fi" for call in translator.calls)


@pytest.mark.asyncio
async def test_finnish_display_output_only_when_requested(monkeypatch):
    monkeypatch.setattr(
        "core.agent_group_call.autocorrect_fi",
        lambda text: "Miten varroapunkkia käsitellään?",
    )
    monkeypatch.setattr(
        "core.agent_group_call.normalize_fi",
        lambda text, sort_words=False: "käsitellä varroa",
    )
    llm = FakeLLM(json.dumps({
        "answers": [
            {
                "agent_id": "beekeeper",
                "answer": "Treat varroa after harvest.",
                "confidence": 0.9,
            },
            {
                "agent_id": "safety",
                "answer": "Use protective equipment.",
                "confidence": 0.8,
            },
        ],
        "synthesis": "Treat safely after harvest.",
    }))
    translator = FakeTranslator()
    pipeline = AgentGroupCallPipeline(llm, translator=translator)

    result = await pipeline.generate_group(
        "Miten varroapunkkia käsitellään?",
        [GroupAgentSlot(agent_id="beekeeper"), GroupAgentSlot(agent_id="safety")],
        output_language="fi",
    )

    assert result.ok is True
    assert result.output_language == "fi"
    assert result.answers[0].answer_en == "Treat varroa after harvest."
    assert result.answers[0].answer_display == "FI:Treat varroa after harvest."
    assert result.synthesis_display == "FI:Treat safely after harvest."
    assert (
        "batch_en_to_fi",
        (
            "Treat varroa after harvest.",
            "Use protective equipment.",
            "Treat safely after harvest.",
        ),
    ) in translator.calls


@pytest.mark.asyncio
async def test_finnish_slot_texts_are_batched_to_english(monkeypatch):
    monkeypatch.setattr("core.agent_group_call.autocorrect_fi", lambda text: text)
    monkeypatch.setattr(
        "core.agent_group_call.normalize_fi",
        lambda text, sort_words=False: "varroa",
    )
    llm = FakeLLM(json.dumps({
        "answers": [
            {
                "agent_id": "beekeeper",
                "answer": "The hive needs a varroa check.",
                "confidence": 0.8,
            }
        ],
        "synthesis": "Route to beekeeper.",
    }))
    translator = FakeTranslator()
    pipeline = AgentGroupCallPipeline(llm, translator=translator)

    result = await pipeline.generate_group(
        "Onko varroa ongelma?",
        [
            GroupAgentSlot(
                agent_id="beekeeper",
                role="Suomen mehiläishoidon asiantuntija",
                system_prompt="Vastaa mehiläispesän hoidosta.",
                context="Pesässä havaittiin punkkeja.",
            )
        ],
        shared_context="Tarhalla on kevät.",
    )

    assert result.ok is True
    prompt = llm.calls[0]["prompt"]
    assert "EN:Suomen mehiläishoidon asiantuntija" in prompt
    assert "EN:Vastaa mehiläispesän hoidosta." in prompt
    assert "EN:Pesässä havaittiin punkkeja." in prompt
    assert "EN:Tarhalla on kevät." in prompt
    assert any(
        call[0] == "batch_fi_to_en"
        and "Suomen mehiläishoidon asiantuntija" in call[1]
        for call in translator.calls
    )


@pytest.mark.asyncio
async def test_english_query_skips_translation_and_keeps_english_display():
    llm = FakeLLM(json.dumps({
        "answers": [
            {
                "agent_id": "meteorologist",
                "answer": "Weather status is stable.",
                "confidence": 0.7,
            }
        ],
        "synthesis": "No translation needed.",
    }))
    translator = FakeTranslator()
    pipeline = AgentGroupCallPipeline(llm, translator=translator)

    result = await pipeline.generate_group(
        "What is the weather status?",
        [GroupAgentSlot(agent_id="meteorologist")],
    )

    assert result.ok is True
    assert result.input_language == "en"
    assert result.query_normalized_fi == ""
    assert result.answers[0].answer_display == "Weather status is stable."
    assert not translator.calls


@pytest.mark.asyncio
async def test_invalid_json_fails_closed():
    llm = FakeLLM("not json")
    pipeline = AgentGroupCallPipeline(llm)

    result = await pipeline.generate_group(
        "What is the status?",
        [GroupAgentSlot(agent_id="core_dispatcher")],
    )

    assert result.ok is False
    assert result.answers == ()
    assert "valid JSON" in result.error
