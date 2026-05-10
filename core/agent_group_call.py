"""Prompt-level group calls for legacy live agents.

This module keeps the LLM-facing group call in English while preserving a
Finnish edge pipeline:

    Finnish user text -> Voikko normalization -> FI->EN translation
    -> one English JSON LLM call for N agent slots
    -> optional EN->FI display translation.

It is intentionally an adapter, not a replacement for ``Agent.think()``.
The fail-closed parser returns ``ok=False`` on malformed LLM output instead
of inventing partial answers.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from core.normalizer import autocorrect_fi, normalize_fi


_FI_CHARS = set("åäöÅÄÖ")
_FI_HINTS = {
    "miten",
    "miksi",
    "mika",
    "mikä",
    "onko",
    "voiko",
    "pitäisi",
    "pitaisi",
    "paljonko",
    "sähkö",
    "sahko",
    "lämpö",
    "lampo",
    "mehiläinen",
    "mehilainen",
}


@dataclass(frozen=True)
class GroupAgentSlot:
    """One agent's compact contribution to a shared LLM request."""

    agent_id: str
    name: str = ""
    role: str = ""
    system_prompt: str = ""
    context: str = ""


@dataclass(frozen=True)
class GroupAnswer:
    """One answer parsed from the group response."""

    agent_id: str
    answer_en: str
    answer_display: str
    confidence: float = 0.0


@dataclass(frozen=True)
class GroupCallResult:
    """Structured result for one prompt-level group call."""

    ok: bool
    input_language: str
    output_language: str
    query_original: str
    query_corrected: str
    query_normalized_fi: str
    query_en: str
    answers: tuple[GroupAnswer, ...]
    synthesis_en: str = ""
    synthesis_display: str = ""
    raw_response: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentGroupCallPipeline:
    """Build and execute one LLM request for a panel of agent slots."""

    def __init__(
        self,
        llm: Any,
        translator: Any | None = None,
        *,
        max_agent_prompt_chars: int = 900,
        max_agent_context_chars: int = 700,
        max_shared_context_chars: int = 1800,
    ) -> None:
        self.llm = llm
        self.translator = translator
        self.max_agent_prompt_chars = max_agent_prompt_chars
        self.max_agent_context_chars = max_agent_context_chars
        self.max_shared_context_chars = max_shared_context_chars

    async def generate_group(
        self,
        user_text: str,
        agent_slots: Iterable[GroupAgentSlot],
        *,
        shared_context: str = "",
        output_language: str = "en",
        max_tokens: int = 1024,
    ) -> GroupCallResult:
        """Run one English JSON LLM call for multiple agent slots."""

        slots = tuple(agent_slots)
        normalized_output_language = _normalize_output_language(output_language)
        if normalized_output_language is None:
            return self._error_result(
                user_text,
                f"unsupported output language: {output_language}",
                output_language="en",
            )
        if not slots:
            return self._error_result(
                user_text,
                "no agent slots supplied",
                output_language=normalized_output_language,
            )

        prepared = self._prepare_query(user_text)
        prompt = self._build_prompt(
            prepared,
            slots,
            shared_context,
            output_language=normalized_output_language,
        )

        response = await self.llm.generate(
            prompt=prompt,
            system=_GROUP_SYSTEM_PROMPT,
            temperature=0.1,
            max_tokens=max_tokens,
        )
        raw = str(getattr(response, "content", response) or "")
        parsed = _parse_group_json(raw)
        if parsed is None:
            return self._error_result(
                user_text,
                "LLM group response was not valid JSON",
                prepared=prepared,
                raw_response=raw,
                output_language=normalized_output_language,
            )

        answers_raw = parsed.get("answers")
        if not isinstance(answers_raw, list):
            return self._error_result(
                user_text,
                "LLM group response missing answers list",
                prepared=prepared,
                raw_response=raw,
                output_language=normalized_output_language,
            )

        output_fi = normalized_output_language == "fi"
        answer_pairs: list[tuple[str, str, float]] = []
        for item in answers_raw:
            if not isinstance(item, dict):
                continue
            agent_id = str(item.get("agent_id") or "").strip()
            answer_en = str(item.get("answer") or "").strip()
            if not agent_id or not answer_en:
                continue
            confidence = _safe_float(item.get("confidence"), default=0.0)
            answer_pairs.append((agent_id, answer_en, confidence))

        if not answer_pairs:
            return self._error_result(
                user_text,
                "LLM group response had no usable answers",
                prepared=prepared,
                raw_response=raw,
                output_language=normalized_output_language,
            )

        synthesis_en = str(parsed.get("synthesis") or "").strip()
        display_texts = [pair[1] for pair in answer_pairs]
        synthesis_index = None
        if synthesis_en:
            synthesis_index = len(display_texts)
            display_texts.append(synthesis_en)
        if output_fi:
            display_texts = self._translate_many("en_to_fi", display_texts)
        answers = tuple(
            GroupAnswer(
                agent_id=agent_id,
                answer_en=answer_en,
                answer_display=display_texts[index],
                confidence=confidence,
            )
            for index, (agent_id, answer_en, confidence)
            in enumerate(answer_pairs)
        )
        synthesis_display = (
            display_texts[synthesis_index]
            if synthesis_index is not None
            else synthesis_en
        )
        return GroupCallResult(
            ok=True,
            input_language=prepared["input_language"],
            output_language=normalized_output_language,
            query_original=user_text,
            query_corrected=prepared["query_corrected"],
            query_normalized_fi=prepared["query_normalized_fi"],
            query_en=prepared["query_en"],
            answers=tuple(answers),
            synthesis_en=synthesis_en,
            synthesis_display=synthesis_display,
            raw_response=raw,
        )

    def _prepare_query(self, user_text: str) -> dict[str, str]:
        original = user_text or ""
        input_language = "fi" if _looks_finnish(original) else "en"
        if input_language == "fi":
            corrected = autocorrect_fi(original)
            normalized = normalize_fi(corrected, sort_words=True)
            query_en = self._translate_many("fi_to_en", [corrected])[0]
        else:
            corrected = original
            normalized = ""
            query_en = original
        return {
            "input_language": input_language,
            "query_original": original,
            "query_corrected": corrected,
            "query_normalized_fi": normalized,
            "query_en": query_en,
        }

    def _build_prompt(
        self,
        prepared: dict[str, str],
        agent_slots: tuple[GroupAgentSlot, ...],
        shared_context: str,
        output_language: str,
    ) -> str:
        slot_lines = self._prepare_agent_slots(agent_slots)
        shared_context_en = (
            self._translate_many("fi_to_en", [shared_context])[0]
            if _looks_finnish(shared_context)
            else shared_context
        )
        payload = {
            "display_output_language": output_language,
            "query_original": prepared["query_original"],
            "query_corrected": prepared["query_corrected"],
            "query_normalized_fi": prepared["query_normalized_fi"],
            "query_en": prepared["query_en"],
            "shared_context_en": _clip(
                shared_context_en,
                self.max_shared_context_chars,
            ),
            "agents": slot_lines,
            "required_output_schema": {
                "answers": [
                    {
                        "agent_id": "agent id from input",
                        "answer": "short English answer",
                        "confidence": 0.0,
                    }
                ],
                "synthesis": "short English synthesis",
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)

    def _prepare_agent_slots(
        self,
        agent_slots: tuple[GroupAgentSlot, ...],
    ) -> list[dict[str, str]]:
        slot_lines: list[dict[str, str]] = []
        pending: list[tuple[int, str]] = []
        for index, slot in enumerate(agent_slots):
            values = {
                "agent_id": slot.agent_id,
                "name": slot.name or slot.agent_id,
                "role": _clip(slot.role, 240),
                "system_prompt_excerpt": _clip(
                    slot.system_prompt,
                    self.max_agent_prompt_chars,
                ),
                "context_excerpt": _clip(slot.context, self.max_agent_context_chars),
            }
            slot_lines.append(values)
            for field in ("role", "system_prompt_excerpt", "context_excerpt"):
                if _looks_finnish(values[field]):
                    pending.append((index, field))
        if pending:
            translated = self._translate_many(
                "fi_to_en",
                [slot_lines[index][field] for index, field in pending],
            )
            for (index, field), value in zip(pending, translated):
                slot_lines[index][field] = value
        return slot_lines

    def _translate_many(self, direction: str, texts: list[str]) -> list[str]:
        if not texts or self.translator is None:
            return list(texts)
        batch_method = (
            "batch_fi_to_en" if direction == "fi_to_en" else "batch_en_to_fi"
        )
        try:
            if hasattr(self.translator, batch_method):
                raw_results = getattr(self.translator, batch_method)(texts)
                if len(raw_results) == len(texts):
                    return [
                        _translation_text(result, original)
                        for result, original in zip(raw_results, texts)
                    ]
        except Exception:
            pass
        return [self._translate(direction, text) for text in texts]

    def _translate(self, direction: str, text: str) -> str:
        if not text or self.translator is None:
            return text
        try:
            if direction == "fi_to_en" and hasattr(self.translator, "fi_to_en"):
                translated = self.translator.fi_to_en(text)
            elif direction == "en_to_fi" and hasattr(self.translator, "en_to_fi"):
                translated = self.translator.en_to_fi(text)
            elif hasattr(self.translator, "execute"):
                result = self.translator.execute(text=text, direction=direction)
                translated = result.get("translated") if isinstance(result, dict) else None
            else:
                translated = None
            return _translation_text(translated, text)
        except Exception:
            return text

    def _error_result(
        self,
        user_text: str,
        error: str,
        *,
        prepared: dict[str, str] | None = None,
        raw_response: str = "",
        output_language: str = "en",
    ) -> GroupCallResult:
        prepared = prepared or {
            "input_language": "fi" if _looks_finnish(user_text) else "en",
            "query_corrected": user_text,
            "query_normalized_fi": "",
            "query_en": user_text,
        }
        return GroupCallResult(
            ok=False,
            input_language=prepared["input_language"],
            output_language=output_language,
            query_original=user_text,
            query_corrected=prepared["query_corrected"],
            query_normalized_fi=prepared["query_normalized_fi"],
            query_en=prepared["query_en"],
            answers=(),
            raw_response=raw_response,
            error=error,
        )


def _looks_finnish(text: str) -> bool:
    lowered = (text or "").lower()
    if any(ch in text for ch in _FI_CHARS):
        return True
    words = set(re.findall(r"[a-zåäö]+", lowered))
    return bool(words & _FI_HINTS)


def _clip(text: str, max_chars: int) -> str:
    value = str(text or "").strip()
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def _safe_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_output_language(language: str) -> str | None:
    normalized = (language or "en").strip().lower()
    if normalized in {"en", "english"}:
        return "en"
    if normalized in {"fi", "finnish", "suomi"}:
        return "fi"
    return None


def _translation_text(result: Any, fallback: str) -> str:
    if result is None:
        return fallback
    if hasattr(result, "text"):
        value = getattr(result, "text")
    elif isinstance(result, dict):
        value = result.get("translated") or result.get("text")
    else:
        value = result
    return str(value or fallback)


def _parse_group_json(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


_GROUP_SYSTEM_PROMPT = (
    "You are WaggleDance AgentPanel. Answer as multiple specialist agents in "
    "one call. Use English internally. Return only valid JSON matching the "
    "required_output_schema. Do not include markdown. Do not invent agent IDs."
)
