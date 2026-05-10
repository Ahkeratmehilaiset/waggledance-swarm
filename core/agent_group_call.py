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
        max_tokens: int = 1024,
    ) -> GroupCallResult:
        """Run one English JSON LLM call for multiple agent slots."""

        slots = tuple(agent_slots)
        if not slots:
            return self._error_result(user_text, "no agent slots supplied")

        prepared = self._prepare_query(user_text)
        prompt = self._build_prompt(prepared, slots, shared_context)

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
            )

        answers_raw = parsed.get("answers")
        if not isinstance(answers_raw, list):
            return self._error_result(
                user_text,
                "LLM group response missing answers list",
                prepared=prepared,
                raw_response=raw,
            )

        output_fi = prepared["input_language"] == "fi"
        answers: list[GroupAnswer] = []
        for item in answers_raw:
            if not isinstance(item, dict):
                continue
            agent_id = str(item.get("agent_id") or "").strip()
            answer_en = str(item.get("answer") or "").strip()
            if not agent_id or not answer_en:
                continue
            confidence = _safe_float(item.get("confidence"), default=0.0)
            display = self._translate("en_to_fi", answer_en) if output_fi else answer_en
            answers.append(GroupAnswer(
                agent_id=agent_id,
                answer_en=answer_en,
                answer_display=display,
                confidence=confidence,
            ))

        if not answers:
            return self._error_result(
                user_text,
                "LLM group response had no usable answers",
                prepared=prepared,
                raw_response=raw,
            )

        synthesis_en = str(parsed.get("synthesis") or "").strip()
        synthesis_display = (
            self._translate("en_to_fi", synthesis_en)
            if output_fi and synthesis_en
            else synthesis_en
        )
        return GroupCallResult(
            ok=True,
            input_language=prepared["input_language"],
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
            query_en = self._translate("fi_to_en", corrected)
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
    ) -> str:
        slot_lines = []
        for slot in agent_slots:
            slot_lines.append({
                "agent_id": slot.agent_id,
                "name": slot.name or slot.agent_id,
                "role": _clip(slot.role, 240),
                "system_prompt_excerpt": _clip(
                    slot.system_prompt,
                    self.max_agent_prompt_chars,
                ),
                "context_excerpt": _clip(slot.context, self.max_agent_context_chars),
            })
        payload = {
            "query_original": prepared["query_original"],
            "query_corrected": prepared["query_corrected"],
            "query_normalized_fi": prepared["query_normalized_fi"],
            "query_en": prepared["query_en"],
            "shared_context": _clip(shared_context, self.max_shared_context_chars),
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
            return str(translated or text)
        except Exception:
            return text

    def _error_result(
        self,
        user_text: str,
        error: str,
        *,
        prepared: dict[str, str] | None = None,
        raw_response: str = "",
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
