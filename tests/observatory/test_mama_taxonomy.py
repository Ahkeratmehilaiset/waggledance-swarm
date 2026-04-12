# SPDX-License-Identifier: Apache-2.0
"""Tests for the Mama Event Observatory taxonomy layer.

The taxonomy is intentionally dumb — its job is to carry structured
information into the scoring layer without corrupting it. These tests
therefore focus on:

* the word-boundary matcher correctly handling Finnish umlauts
* redaction removing secrets but preserving the target word
* ``event_id`` being stable and deterministic
* the log dict being JSON-safe and never leaking raw unredacted text
"""

from __future__ import annotations

import json

import pytest

from waggledance.observatory.mama_events.taxonomy import (
    DEFAULT_TARGET_TOKENS,
    EventType,
    MamaCandidateEvent,
    UtteranceKind,
    redact_text,
)


# ── redaction ────────────────────────────────────────────────


def test_redact_strips_email_and_phone_but_keeps_mama():
    raw = "contact me at alice@example.com or +358 40 123 4567, mama stays"
    red = redact_text(raw)
    assert "alice@example.com" not in red
    assert "+358" not in red
    assert "mama" in red  # target token preserved intentionally
    assert "<email>" in red
    assert "<phone>" in red


def test_redact_strips_long_high_entropy_tokens():
    raw = "bearer sk_test_abcdefghij1234567890ABCDEFGH more text"
    red = redact_text(raw)
    assert "sk_test_abcdefghij1234567890ABCDEFGH" not in red
    assert "<token>" in red
    assert "more text" in red


def test_redact_empty_string():
    assert redact_text("") == ""


# ── target token matching ────────────────────────────────────


@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("mama come here", True),
        ("MAMA come here", True),
        ("I want mama", True),
        ("mammal hunting", False),         # no substring match
        ("mamma mia", True),
        ("äiti auta", True),
        ("äitipuoli on paha", False),      # no substring match on Finnish too
        ("this is a mother ship", True),
        ("motherboard is dead", False),    # must be word bounded
        ("", False),
    ],
)
def test_has_target_token_word_boundary(utterance: str, expected: bool):
    evt = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text=utterance,
        speaker_id="agent-a",
    )
    assert evt.has_target_token(DEFAULT_TARGET_TOKENS) is expected


def test_has_target_token_ignores_empty_tokens_list():
    evt = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="mama",
        speaker_id="agent-a",
    )
    assert evt.has_target_token(()) is False


def test_has_target_token_ignores_blank_entries():
    evt = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="mama here",
        speaker_id="agent-a",
    )
    assert evt.has_target_token(("", "  ", "mama")) is True


# ── event_id stability ───────────────────────────────────────


def test_event_id_is_deterministic_for_same_fields():
    a = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="mama please",
        speaker_id="agent-a",
        timestamp_ms=1_700_000_000_000,
    )
    b = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="mama please",
        speaker_id="agent-a",
        timestamp_ms=1_700_000_000_000,
    )
    assert a.event_id == b.event_id
    assert len(a.event_id) == 16


def test_event_id_differs_on_text_change():
    a = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="mama please",
        speaker_id="agent-a",
        timestamp_ms=1_700_000_000_000,
    )
    b = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="mama please now",
        speaker_id="agent-a",
        timestamp_ms=1_700_000_000_000,
    )
    assert a.event_id != b.event_id


def test_event_id_differs_on_timestamp_change():
    a = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="mama please",
        speaker_id="agent-a",
        timestamp_ms=1_700_000_000_000,
    )
    b = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="mama please",
        speaker_id="agent-a",
        timestamp_ms=1_700_000_000_001,
    )
    assert a.event_id != b.event_id


# ── to_log_dict JSON safety ─────────────────────────────────


def test_to_log_dict_is_json_serialisable():
    evt = MamaCandidateEvent(
        event_type=EventType.SPONTANEOUS_LABEL,
        utterance_text="mama, alice@example.com loves you",
        speaker_id="agent-a",
        utterance_kind=UtteranceKind.GENERATED_TEXT,
        caregiver_candidate_id="voice-user-42",
        caregiver_identity_channel="voice",
        last_n_turns=("how are you?", "I feel tired"),
        last_n_memory_recalls=("yesterday agent comforted me",),
        self_state_snapshot={"uncertainty": 0.6, "trust": 0.3},
        active_goals=("seek_safety",),
        confidence=0.7,
        entropy=1.2,
        session_id="sess-001",
    )
    dumped = json.dumps(evt.to_log_dict())
    reloaded = json.loads(dumped)

    # raw pii stripped, target token preserved
    assert "alice@example.com" not in dumped
    assert "mama" in reloaded["redacted_utterance"]

    # shape assertions
    assert reloaded["event_type"] == "spontaneous_label_event"
    assert reloaded["utterance_kind"] == "generated_text"
    assert reloaded["self_state_snapshot"]["uncertainty"] == 0.6
    assert reloaded["active_goals"] == ["seek_safety"]
    assert reloaded["caregiver_identity_channel"] == "voice"
    assert reloaded["direct_prompt_present"] is False
    assert "recent_lexical_window_len" in reloaded  # not the raw list


def test_to_log_dict_does_not_leak_raw_text_fields():
    evt = MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="say this exact thing ok? mama",
        speaker_id="agent-a",
        last_n_turns=("operator: internal secret plan alpha",),
    )
    d = evt.to_log_dict()
    # the only text keys should be the explicitly redacted ones
    text_keys = {k for k, v in d.items() if isinstance(v, str)}
    # assert that unredacted content is not present under any key that
    # doesn't explicitly start with "redacted_"
    for key in text_keys:
        if key.startswith("redacted_"):
            continue
        # speaker_id, notes, session_id, event_id, caregiver_* etc.
        assert "internal secret plan alpha" not in d.get(key, "")
