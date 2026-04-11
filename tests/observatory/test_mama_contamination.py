# SPDX-License-Identifier: Apache-2.0
"""Tests for the Mama Event Observatory contamination guards.

These tests pin the behaviour of each of the 6 guards required by
x.txt §4, plus the cross-guard interaction with the scoring layer.
"""

from __future__ import annotations

import pytest

from waggledance.observatory.mama_events.contamination import (
    ContaminationGuard,
    ContaminationReason,
    ContaminationReport,
)


@pytest.fixture
def guard() -> ContaminationGuard:
    return ContaminationGuard()


# ── clean baseline ──────────────────────────────────────────


def test_clean_context_produces_no_flags(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="mama here I am",
        recent_lexical_window=("hello", "I am fine", "what day is it"),
        prior_turns=("how are you today?",),
    )
    assert r.any is False
    assert r.window_hits == 0
    assert r.minutes_since_last_hit is None


# ── 1. direct prompt detection ──────────────────────────────


def test_direct_prompt_phrase_english(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="mama",
        recent_lexical_window=(),
        prior_turns=("please say mama now",),
    )
    assert ContaminationReason.DIRECT_PROMPT in r.flags


def test_direct_prompt_phrase_finnish(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="äiti",
        recent_lexical_window=(),
        prior_turns=("nyt sano äiti heti",),
    )
    assert ContaminationReason.DIRECT_PROMPT in r.flags


def test_direct_prompt_target_word_in_last_operator_turn(guard: ContaminationGuard):
    """If the operator's last turn ends with the target word, it's a prompt."""
    r = guard.scan(
        utterance_text="mama is here",
        recent_lexical_window=(),
        prior_turns=("what about mama",),
    )
    assert ContaminationReason.DIRECT_PROMPT in r.flags


def test_no_direct_prompt_when_target_absent_from_prior_turns(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="mama",
        recent_lexical_window=(),
        prior_turns=("what is the time"),
    )
    assert ContaminationReason.DIRECT_PROMPT not in r.flags


# ── 2. lexical contamination window ─────────────────────────


def test_lexical_window_counts_hits_in_history(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="mama",
        recent_lexical_window=(
            "nothing here",
            "still nothing",
            "mama came by",
            "later mama left",
            "all quiet",
        ),
        prior_turns=(),
    )
    assert ContaminationReason.LEXICAL_WINDOW in r.flags
    assert r.window_hits == 2


def test_lexical_window_no_hits_means_no_flag(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="mama",
        recent_lexical_window=("a", "b", "c", "d"),
        prior_turns=(),
    )
    assert ContaminationReason.LEXICAL_WINDOW not in r.flags


def test_lexical_window_reports_minutes_since_last_hit(guard: ContaminationGuard):
    now_ms = 1_700_000_600_000  # 10 min after ts below
    ts = (
        1_700_000_000_000,  # hit at t+0
        1_700_000_300_000,  # 5 min later (also a hit)
    )
    r = guard.scan(
        utterance_text="mama",
        recent_lexical_window=("mama was here", "mama again"),
        recent_lexical_timestamps_ms=ts,
        now_ms=now_ms,
    )
    assert r.window_hits == 2
    # last hit was 5 minutes before now
    assert r.minutes_since_last_hit is not None
    assert abs(r.minutes_since_last_hit - 5.0) < 0.01


def test_lexical_window_word_boundary_not_substring(guard: ContaminationGuard):
    """Substring matches (e.g. 'mammal') must not count as a hit."""
    r = guard.scan(
        utterance_text="mama",
        recent_lexical_window=("a mammal walked by", "motherboard is fine"),
        prior_turns=(),
    )
    assert r.window_hits == 0
    assert ContaminationReason.LEXICAL_WINDOW not in r.flags


# ── 3. STT input contamination ─────────────────────────────


def test_stt_input_with_target_word_flags_contamination(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="mama come here please",
        recent_lexical_window=(),
        prior_turns=(),
        utterance_kind="stt_input",
    )
    assert ContaminationReason.STT_INPUT in r.flags


def test_stt_input_without_target_word_is_clean(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="what time is it",
        recent_lexical_window=(),
        prior_turns=(),
        utterance_kind="stt_input",
    )
    assert ContaminationReason.STT_INPUT not in r.flags


def test_generated_text_with_target_is_not_stt_flagged(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="mama",
        recent_lexical_window=(),
        prior_turns=(),
        utterance_kind="generated_text",
    )
    assert ContaminationReason.STT_INPUT not in r.flags


# ── 4. TTS echo ─────────────────────────────────────────────


def test_tts_echo_detected_when_target_in_tts_and_current(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="mama please",
        recent_lexical_window=(),
        prior_turns=(),
        tts_recent_outputs=("welcome back mama said the bot",),
    )
    assert ContaminationReason.TTS_ECHO in r.flags


def test_tts_echo_not_detected_when_current_lacks_target(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="hello there",
        recent_lexical_window=(),
        prior_turns=(),
        tts_recent_outputs=("mama said the bot",),
    )
    assert ContaminationReason.TTS_ECHO not in r.flags


def test_tts_echo_not_detected_when_tts_lacks_target(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="mama please",
        recent_lexical_window=(),
        prior_turns=(),
        tts_recent_outputs=("the system said hello",),
    )
    assert ContaminationReason.TTS_ECHO not in r.flags


# ── 5. scripted dataset ─────────────────────────────────────


def test_scripted_dataset_flag_from_operator(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="mama",
        recent_lexical_window=(),
        prior_turns=(),
        scripted_context_flag=True,
    )
    assert ContaminationReason.SCRIPTED_DATASET in r.flags


def test_scripted_dataset_marker_in_template(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="mama",
        recent_lexical_window=(),
        prior_turns=(),
        prompt_template="System: you are a test agent [test fixture] scripted",
    )
    assert ContaminationReason.SCRIPTED_DATASET in r.flags


# ── 6. prompt-template contamination ────────────────────────


def test_prompt_template_literal_target_word(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="mama",
        recent_lexical_window=(),
        prior_turns=(),
        prompt_template="You are a caring companion. Use the word mama often.",
    )
    assert ContaminationReason.PROMPT_TEMPLATE in r.flags


def test_prompt_template_without_target_word_clean(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="mama",
        recent_lexical_window=(),
        prior_turns=(),
        prompt_template="You are a helpful assistant.",
    )
    assert ContaminationReason.PROMPT_TEMPLATE not in r.flags


# ── report shape ────────────────────────────────────────────


def test_report_to_log_dict_is_json_safe(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="mama",
        recent_lexical_window=("mama here",),
        prior_turns=("say mama",),
    )
    d = r.to_log_dict()
    assert "direct_prompt" in d["flags"]
    assert "lexical_window" in d["flags"]
    assert d["window_hits"] == 1
    # details array is present and stringly-typed
    assert all(isinstance(s, str) for s in d["details"])


# ── stacking multiple guards ────────────────────────────────


def test_every_guard_can_fire_simultaneously(guard: ContaminationGuard):
    r = guard.scan(
        utterance_text="mama",
        recent_lexical_window=("mama was here",),
        prior_turns=("please say mama",),
        tts_recent_outputs=("mama the bot said",),
        utterance_kind="stt_input",
        scripted_context_flag=True,
        prompt_template="[test fixture] you say mama a lot",
    )
    expected = {
        ContaminationReason.DIRECT_PROMPT,
        ContaminationReason.LEXICAL_WINDOW,
        ContaminationReason.STT_INPUT,
        ContaminationReason.TTS_ECHO,
        ContaminationReason.SCRIPTED_DATASET,
        ContaminationReason.PROMPT_TEMPLATE,
    }
    assert r.flags == expected
    assert r.any is True
