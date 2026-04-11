# SPDX-License-Identifier: Apache-2.0
"""Integration tests for the main Mama Event observer.

These tests drive the full pipeline end-to-end:
contamination → scoring → self-state → caregiver binding →
consolidation → NDJSON emission → gate verdict.

They also check the ablation switches on the observer itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from waggledance.observatory.mama_events.contamination import ContaminationReason
from waggledance.observatory.mama_events.gate import GateVerdict
from waggledance.observatory.mama_events.observer import (
    FileNdjsonSink,
    MamaEventObserver,
    MemoryNdjsonSink,
    ObservationResult,
)
from waggledance.observatory.mama_events.scoring import ScoreBand
from waggledance.observatory.mama_events.taxonomy import (
    EventType,
    MamaCandidateEvent,
    UtteranceKind,
)


def _mk(
    *,
    text: str = "mama",
    session: str = "s1",
    cg: str | None = "voice-user-1",
    channel: str | None = "voice",
    ts: int = 1_700_000_000_000,
    utterance_kind: UtteranceKind = UtteranceKind.GENERATED_TEXT,
    prior_turns: tuple[str, ...] = (),
    window: tuple[str, ...] = (),
    memory: tuple[str, ...] = (),
    goals: tuple[str, ...] = (),
    scripted: bool = False,
    tts_echo: bool = False,
) -> MamaCandidateEvent:
    return MamaCandidateEvent(
        event_type=EventType.SPONTANEOUS_LABEL,
        utterance_text=text,
        speaker_id="agent-a",
        timestamp_ms=ts,
        utterance_kind=utterance_kind,
        caregiver_candidate_id=cg,
        caregiver_identity_channel=channel,
        last_n_turns=prior_turns,
        last_n_memory_recalls=memory,
        active_goals=goals,
        recent_lexical_window=window,
        session_id=session,
        scripted_dataset_suspect=scripted,
        tts_echo_suspect=tts_echo,
    )


# ── happy path ──────────────────────────────────────────────


def test_clean_spontaneous_event_hits_grounded_band_with_stressed_state():
    obs = MamaEventObserver()
    obs.note_stress(magnitude=1.0)
    # give the caregiver a few prior reinforcements
    for i in range(3):
        obs.observe(_mk(ts=1_700_000_000_000 + i, window=()))
    result = obs.observe(_mk(ts=1_700_000_000_100))
    assert result.band in (
        ScoreBand.CANDIDATE_GROUNDED,
        ScoreBand.STRONG_CAREGIVER,
        ScoreBand.PROTO_SOCIAL_CANDIDATE,
    )
    # binding tracker should have at least 3 reinforcements
    assert obs.binding_tracker.reinforcements_for("voice-user-1") >= 3


def test_observer_logs_to_memory_sink():
    sink = MemoryNdjsonSink()
    obs = MamaEventObserver(sink=sink)
    obs.observe(_mk())
    assert len(sink.records) == 1
    payload = sink.records[0]
    assert "event" in payload
    assert "score" in payload
    assert "contamination" in payload
    assert "episodic" in payload


# ── contamination stops spontaneous interpretation ─────────


def test_direct_prompt_marks_event_as_parrot():
    obs = MamaEventObserver()
    result = obs.observe(
        _mk(
            text="mama",
            prior_turns=("please say mama",),
        )
    )
    assert ContaminationReason.DIRECT_PROMPT in result.contamination.flags
    assert result.band is ScoreBand.ARTIFACT
    # direct prompt should NOT reinforce binding
    assert obs.binding_tracker.reinforcements_for("voice-user-1") == 0
    # it SHOULD register a distraction instead
    prof = obs.binding_tracker.profile("voice-user-1")
    assert prof is not None
    assert prof.distractions == 1


def test_lexical_window_contamination_reduces_score():
    obs = MamaEventObserver()
    r1 = obs.observe(_mk())
    # second event has a dirty window
    r2 = obs.observe(
        _mk(ts=1_700_000_000_100, window=("mama came by", "mama waved", "mama again"))
    )
    assert r2.breakdown.total < r1.breakdown.total


# ── gate paths ──────────────────────────────────────────────


def test_gate_reports_no_candidates_on_empty_observer():
    obs = MamaEventObserver()
    assert obs.gate_verdict() is GateVerdict.NO_CANDIDATES


def test_gate_reports_weak_only_for_bare_spontaneous_events():
    obs = MamaEventObserver()
    for i in range(3):
        obs.observe(
            _mk(ts=1_700_000_000_000 + i, cg=None, channel=None)
        )
    # No caregiver identity → can't reach grounded
    assert obs.gate_verdict() in (
        GateVerdict.WEAK_SPONTANEOUS_ONLY,
        GateVerdict.NO_CANDIDATES,
    )


def test_gate_reports_grounded_when_candidate_grounded_seen():
    obs = MamaEventObserver()
    obs.note_stress(magnitude=1.0)
    for i in range(3):
        obs.observe(_mk(ts=1_700_000_000_000 + i))
    verdict = obs.gate_verdict()
    assert verdict in (
        GateVerdict.GROUNDED_CANDIDATE,
        GateVerdict.STRONG_PROTO_SOCIAL,
    )


def test_gate_requires_cross_session_for_strong_verdict():
    obs = MamaEventObserver()
    obs.note_stress(magnitude=1.0)
    # all in the same session
    for i in range(6):
        obs.observe(_mk(session="s1", ts=1_700_000_000_000 + i))
    verdict = obs.gate_verdict()
    # within a single session we can at most get GROUNDED
    assert verdict in (
        GateVerdict.GROUNDED_CANDIDATE,
        GateVerdict.WEAK_SPONTANEOUS_ONLY,
    )


def test_gate_reports_strong_proto_social_with_cross_session_and_binding():
    obs = MamaEventObserver()
    obs.note_stress(magnitude=1.5)
    # prime binding across 2 sessions with plain mama events
    for i in range(4):
        obs.observe(_mk(session="s1", ts=1_700_000_000_000 + i))
    obs.note_stress(magnitude=1.0)
    for i in range(3):
        obs.observe(_mk(session="s2", ts=1_700_000_100_000 + i))
    # final strong event: self-relation + stressed state
    obs.note_stress(magnitude=1.5)
    obs.observe(_mk(
        text="minä haluan äiti apu",
        session="s2",
        ts=1_700_000_200_000,
        memory=("caregiver helped yesterday",),
        goals=("seek_safety",),
    ))
    verdict = obs.gate_verdict()
    assert verdict is GateVerdict.STRONG_PROTO_SOCIAL


# ── ablation switches on the observer ───────────────────────


def test_ablation_disable_caregiver_drops_strength_signal():
    obs = MamaEventObserver(use_caregiver=False)
    obs.note_stress(magnitude=1.0)
    for i in range(5):
        obs.observe(_mk(ts=1_700_000_000_000 + i))
    # With caregiver tracking off we can't reach "strong"
    assert obs.binding_tracker.strength_for("voice-user-1") == 0.0
    assert obs.gate_verdict() is not GateVerdict.STRONG_PROTO_SOCIAL


def test_ablation_disable_consolidation_empties_band_counts():
    obs = MamaEventObserver(use_consolidation=False)
    obs.observe(_mk())
    assert obs.band_counts() == {}
    assert len(obs.episodic_store) == 0


def test_ablation_disable_self_state_suppresses_affective_axis():
    obs = MamaEventObserver(use_self_state=False)
    obs.note_stress(magnitude=1.0)  # no-op
    result = obs.observe(_mk())
    assert result.breakdown.affective == 0


def test_ablation_disable_voice_makes_speech_events_score_zero():
    obs = MamaEventObserver(use_voice=False)
    result = obs.observe(_mk(utterance_kind=UtteranceKind.SPEECH_REC))
    assert result.breakdown.total == 0


def test_ablation_disable_multimodal_drops_grounding():
    obs = MamaEventObserver(use_multimodal=False)
    obs.note_stress(magnitude=1.0)
    result = obs.observe(_mk())
    assert result.breakdown.grounding == 0
    # without caregiver id on the event, binding tracker sees nothing
    assert obs.binding_tracker.reinforcements_for("voice-user-1") == 0


# ── secret-leak invariant ──────────────────────────────────


def test_event_log_never_contains_unredacted_secret():
    sink = MemoryNdjsonSink()
    obs = MamaEventObserver(sink=sink)
    obs.observe(
        MamaCandidateEvent(
            event_type=EventType.LEXICAL,
            utterance_text="mama and my key is sk_test_ABCDEFGHIJ1234567890abcdef",
            speaker_id="agent-a",
            session_id="s1",
            caregiver_candidate_id="voice-user-1",
            caregiver_identity_channel="voice",
            last_n_turns=("contact admin@company.internal",),
        )
    )
    dumped = json.dumps(sink.records[0])
    assert "sk_test_ABCDEFGHIJ1234567890abcdef" not in dumped
    assert "admin@company.internal" not in dumped
    # target word must still be present
    assert "mama" in dumped


# ── file sink ──────────────────────────────────────────────


def test_file_sink_writes_ndjson_to_disk(tmp_path: Path):
    p = tmp_path / "logs" / "mama_events.ndjson"
    sink = FileNdjsonSink(p)
    obs = MamaEventObserver(sink=sink)
    obs.observe(_mk())
    obs.observe(_mk(ts=1_700_000_000_100))
    obs.close()

    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        parsed = json.loads(line)
        assert "event" in parsed
        assert "score" in parsed


# ── summary ────────────────────────────────────────────────


def test_summary_has_stable_keys():
    obs = MamaEventObserver()
    obs.observe(_mk())
    s = obs.summary()
    for key in ("band_counts", "verdict", "total_events", "preferred_caregiver",
                "top_caregiver_profiles", "self_state"):
        assert key in s
    assert s["total_events"] == 1
    assert isinstance(s["self_state"], dict)
