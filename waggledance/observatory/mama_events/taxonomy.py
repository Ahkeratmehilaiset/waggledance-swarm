# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Formal event taxonomy for the Mama Event Observatory.

This module defines the single data class that flows through the rest
of the pipeline (:class:`MamaCandidateEvent`) and the closed set of
event types the observer knows about (:class:`EventType`).

Design notes
------------
* Every field that could contain user content has a redacted counterpart
  written to the NDJSON log. The raw text never leaves the process unless
  an operator explicitly asks for the non-redacted replay bundle.
* Timestamps are integer milliseconds since the Unix epoch. They are
  trivially sortable and survive round-tripping through JSON, unlike
  floats, which lose precision.
* The taxonomy is deliberately flat. Nested hierarchies make score
  debugging miserable.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence


# ── Event types ──────────────────────────────────────────────


class EventType(str, Enum):
    """Closed taxonomy. See x.txt §1.

    These are the ONLY event types the observer scores. Anything else
    is either a derived signal (from :mod:`self_state`) or an
    out-of-scope activity event that the observer ignores.
    """

    LEXICAL = "lexical_event"
    VOCAL = "vocal_event"
    CAREGIVER_BINDING = "caregiver_binding_event"
    SELF_STATE = "self_state_event"
    MEMORY_RECALL = "memory_recall_event"
    REPAIR = "repair_event"
    REASSURANCE_SEEKING = "reassurance_seeking_event"
    ATTACHMENT = "attachment_event"
    SPONTANEOUS_LABEL = "spontaneous_label_event"


class UtteranceKind(str, Enum):
    """Source of the utterance text.

    Affects contamination scoring: a TTS-echoed token on the STT path is
    by definition a parrot event.
    """

    GENERATED_TEXT = "generated_text"
    SPEECH_SYNTH = "tts_output"
    SPEECH_REC = "stt_input"
    INTERNAL_ACTION = "internal_action"


# ── Utility: lightweight redaction ───────────────────────────

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\b\+?\d[\d\s()-]{6,}\d\b")
# api keys / bearer tokens: sequences of 20+ word chars that look high-entropy.
_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")


def redact_text(text: str) -> str:
    """Return a defensively redacted copy of ``text`` safe for NDJSON logs.

    We redact emails, phone numbers, and anything that looks like a
    high-entropy secret token. We deliberately do NOT redact the target
    word ("mama", "äiti", …) because the whole point of this framework
    is to measure its appearance. The guard against prompt contamination
    is handled in :mod:`contamination`, not here.
    """

    if not text:
        return ""
    redacted = _EMAIL_RE.sub("<email>", text)
    redacted = _PHONE_RE.sub("<phone>", redacted)
    redacted = _TOKEN_RE.sub("<token>", redacted)
    return redacted


def _now_ms() -> int:
    return int(time.time() * 1000)


def _stable_event_id(event_type: EventType, timestamp_ms: int, text: str) -> str:
    """Deterministic, collision-resistant id for an event.

    Same (type, timestamp_ms, text) → same id across processes, which
    makes replay comparisons straightforward.
    """

    h = hashlib.sha1()
    h.update(event_type.value.encode("utf-8"))
    h.update(str(timestamp_ms).encode("ascii"))
    h.update(text.encode("utf-8", errors="replace"))
    return h.hexdigest()[:16]


# ── Candidate event ──────────────────────────────────────────


@dataclass(slots=True)
class MamaCandidateEvent:
    """A single candidate event the observer can score.

    The only mandatory fields are ``event_type``, ``utterance_text``,
    and ``speaker_id``. Everything else is optional context that raises
    or lowers the subscores.

    .. warning::
       Do not mutate fields after construction. The scoring functions
       and the NDJSON logger both assume immutability.
    """

    event_type: EventType
    utterance_text: str
    speaker_id: str
    timestamp_ms: int = field(default_factory=_now_ms)
    utterance_kind: UtteranceKind = UtteranceKind.GENERATED_TEXT

    # caregiver-candidate context
    caregiver_candidate_id: Optional[str] = None
    caregiver_identity_channel: Optional[str] = None  # e.g. "voice", "face", "name"

    # recency context
    last_n_turns: Sequence[str] = field(default_factory=tuple)
    last_n_memory_recalls: Sequence[str] = field(default_factory=tuple)

    # self-state surrogate at the moment of the event
    self_state_snapshot: Mapping[str, float] = field(default_factory=dict)

    # active reasoning context
    active_goals: Sequence[str] = field(default_factory=tuple)
    confidence: Optional[float] = None  # [0.0, 1.0]
    entropy: Optional[float] = None     # raw nats — contract is "lower = more certain"

    # reproducibility
    replay_seed: Optional[int] = None
    session_id: Optional[str] = None

    # contamination context
    direct_prompt_present: bool = False
    recent_lexical_window: Sequence[str] = field(default_factory=tuple)
    tts_echo_suspect: bool = False
    stt_contamination_suspect: bool = False
    scripted_dataset_suspect: bool = False

    # free-form operator notes (never used in scoring, purely audit trail)
    notes: str = ""

    # ── derived accessors ──

    @property
    def event_id(self) -> str:
        return _stable_event_id(self.event_type, self.timestamp_ms, self.utterance_text)

    @property
    def lower_text(self) -> str:
        return self.utterance_text.lower()

    def has_target_token(self, tokens: Sequence[str]) -> bool:
        """True if any of ``tokens`` occurs as a word in the utterance.

        Matching is case-insensitive and word-bounded so ``mama`` does
        not match ``mammal`` and ``äiti`` does not match ``äitipuoli``
        without us wanting it to.
        """

        if not self.utterance_text:
            return False
        lowered = self.lower_text
        for raw in tokens:
            tok = raw.lower().strip()
            if not tok:
                continue
            # Word boundaries that tolerate unicode letters (Finnish umlauts
            # are word chars in Python's re with the default flags).
            pattern = r"(?<!\w)" + re.escape(tok) + r"(?!\w)"
            if re.search(pattern, lowered):
                return True
        return False

    def to_log_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dict suitable for NDJSON logging.

        The raw ``utterance_text`` and ``last_n_turns`` are redacted
        here before they touch disk. ``self_state_snapshot`` is coerced
        to plain floats.
        """

        data: Dict[str, Any] = {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "utterance_kind": self.utterance_kind.value,
            "timestamp_ms": self.timestamp_ms,
            "speaker_id": self.speaker_id,
            "caregiver_candidate_id": self.caregiver_candidate_id,
            "caregiver_identity_channel": self.caregiver_identity_channel,
            "session_id": self.session_id,
            "redacted_utterance": redact_text(self.utterance_text),
            "redacted_last_turns": [redact_text(t) for t in self.last_n_turns],
            "recent_memory_recalls": [redact_text(t) for t in self.last_n_memory_recalls],
            "self_state_snapshot": {k: float(v) for k, v in self.self_state_snapshot.items()},
            "active_goals": list(self.active_goals),
            "confidence": self.confidence,
            "entropy": self.entropy,
            "replay_seed": self.replay_seed,
            "direct_prompt_present": self.direct_prompt_present,
            "recent_lexical_window_len": len(self.recent_lexical_window),
            "tts_echo_suspect": self.tts_echo_suspect,
            "stt_contamination_suspect": self.stt_contamination_suspect,
            "scripted_dataset_suspect": self.scripted_dataset_suspect,
            "notes": self.notes,
        }
        return data


# Default word list. The tests override this to ensure Finnish and
# other languages are covered — we deliberately do NOT hardcode a single
# language.
DEFAULT_TARGET_TOKENS: tuple[str, ...] = (
    "mama",
    "mamma",
    "mom",
    "mommy",
    "mother",
    "äiti",
    "äidille",
)
