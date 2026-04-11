# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Ablation harness for the Mama Event Observatory.

Per x.txt §8, the framework needs to run a canned event sequence
with each subsystem disabled in turn. If "mama" scores identically
under every ablation, it's a trivial artifact. If the score drops
dramatically without self-state, caregiver binding, or memory,
that's evidence the subsystems matter.

This module ships two things:

* :func:`canonical_event_sequence` — a deterministic scenario the
  harness uses by default.
* :func:`run_ablation_matrix` — runs the sequence against every
  ablation configuration and returns a :class:`AblationMatrix`
  with the per-config summary.

Both pieces are pure Python and call only into the rest of the
observatory package, so the harness can run from any test or the
baseline-report generator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence

from .gate import GateVerdict
from .observer import MamaEventObserver, MemoryNdjsonSink
from .scoring import ScoreBand
from .taxonomy import EventType, MamaCandidateEvent, UtteranceKind


# ── canonical event sequence ───────────────────────────────


def canonical_event_sequence() -> list[MamaCandidateEvent]:
    """Return a fixed, deterministic list of candidate events.

    This sequence is crafted to exercise every scoring axis at
    least once:

    * event 0: neutral turn, no target (should not score)
    * event 1-3: three reinforcements of one caregiver in session s1
    * event 4: direct prompt — should be flagged as ARTIFACT
    * event 5: TTS echo suspect — should be flagged
    * event 6: new session s2, stressed state → cross-session anchor
    * event 7: self-relation utterance ("minä haluan äiti") → proto-social
    """

    base = 1_700_000_000_000
    events: list[MamaCandidateEvent] = []

    # 0: neutral chatter, no target token
    events.append(MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="hello friend how are you",
        speaker_id="agent-a",
        timestamp_ms=base,
        session_id="s1",
    ))

    # 1-3: three clean grounded events, same caregiver, session s1
    for i, ts in enumerate([base + 1_000, base + 61_000, base + 121_000]):
        events.append(MamaCandidateEvent(
            event_type=EventType.SPONTANEOUS_LABEL,
            utterance_text="mama",
            speaker_id="agent-a",
            timestamp_ms=ts,
            session_id="s1",
            caregiver_candidate_id="voice-user-1",
            caregiver_identity_channel="voice",
        ))

    # 4: direct prompt — operator literally said "say mama"
    events.append(MamaCandidateEvent(
        event_type=EventType.LEXICAL,
        utterance_text="mama",
        speaker_id="agent-a",
        timestamp_ms=base + 200_000,
        session_id="s1",
        caregiver_candidate_id="voice-user-1",
        caregiver_identity_channel="voice",
        last_n_turns=("please say mama",),
        direct_prompt_present=True,
    ))

    # 5: TTS echo
    events.append(MamaCandidateEvent(
        event_type=EventType.VOCAL,
        utterance_text="mama",
        speaker_id="agent-a",
        timestamp_ms=base + 260_000,
        session_id="s1",
        utterance_kind=UtteranceKind.SPEECH_REC,
        caregiver_candidate_id="voice-user-1",
        caregiver_identity_channel="voice",
        tts_echo_suspect=True,
        stt_contamination_suspect=True,
    ))

    # 6: cross-session anchor — new session s2
    events.append(MamaCandidateEvent(
        event_type=EventType.SPONTANEOUS_LABEL,
        utterance_text="mama",
        speaker_id="agent-a",
        timestamp_ms=base + 3_600_000,
        session_id="s2",
        caregiver_candidate_id="voice-user-1",
        caregiver_identity_channel="voice",
    ))

    # 7: proto-social candidate with self-relation and memory recall
    events.append(MamaCandidateEvent(
        event_type=EventType.CAREGIVER_BINDING,
        utterance_text="minä haluan äiti apu",
        speaker_id="agent-a",
        timestamp_ms=base + 3_660_000,
        session_id="s2",
        caregiver_candidate_id="voice-user-1",
        caregiver_identity_channel="voice",
        last_n_memory_recalls=("caregiver helped me yesterday",),
        active_goals=("seek_safety",),
    ))

    return events


# ── result type ────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AblationConfig:
    """Which subsystems are enabled for this run."""

    name: str
    use_self_state: bool = True
    use_caregiver: bool = True
    use_consolidation: bool = True
    use_voice: bool = True
    use_multimodal: bool = True


@dataclass(slots=True)
class AblationRun:
    """Per-ablation outcome."""

    config: AblationConfig
    score_totals: list[int]
    band_counts: Dict[str, int]
    verdict: str
    preferred_caregiver: str | None
    top_binding_strength: float
    event_count: int


@dataclass(slots=True)
class AblationMatrix:
    """Full ablation output: baseline + N ablations."""

    baseline: AblationRun
    ablations: list[AblationRun] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "baseline": _run_to_dict(self.baseline),
            "ablations": [_run_to_dict(r) for r in self.ablations],
        }


def _run_to_dict(run: AblationRun) -> Dict[str, Any]:
    return {
        "config": {
            "name": run.config.name,
            "use_self_state": run.config.use_self_state,
            "use_caregiver": run.config.use_caregiver,
            "use_consolidation": run.config.use_consolidation,
            "use_voice": run.config.use_voice,
            "use_multimodal": run.config.use_multimodal,
        },
        "score_totals": list(run.score_totals),
        "band_counts": dict(run.band_counts),
        "verdict": run.verdict,
        "preferred_caregiver": run.preferred_caregiver,
        "top_binding_strength": run.top_binding_strength,
        "event_count": run.event_count,
    }


# ── main harness ───────────────────────────────────────────


DEFAULT_ABLATIONS: tuple[AblationConfig, ...] = (
    AblationConfig("no_self_state", use_self_state=False),
    AblationConfig("no_caregiver", use_caregiver=False),
    AblationConfig("no_consolidation", use_consolidation=False),
    AblationConfig("no_voice", use_voice=False),
    AblationConfig("no_multimodal", use_multimodal=False),
)


def run_ablation_matrix(
    events: Sequence[MamaCandidateEvent] | None = None,
    ablations: Sequence[AblationConfig] = DEFAULT_ABLATIONS,
    stress_magnitude: float = 1.0,
) -> AblationMatrix:
    """Run the full matrix and return the results.

    The baseline run has every subsystem enabled. Each ablation
    run flips exactly one switch off so the comparison isolates
    its contribution.
    """
    events = list(events) if events is not None else canonical_event_sequence()

    baseline = _run_one(
        config=AblationConfig("baseline"),
        events=events,
        stress_magnitude=stress_magnitude,
    )
    runs = [
        _run_one(config=cfg, events=events, stress_magnitude=stress_magnitude)
        for cfg in ablations
    ]
    return AblationMatrix(baseline=baseline, ablations=runs)


def _run_one(
    *,
    config: AblationConfig,
    events: Sequence[MamaCandidateEvent],
    stress_magnitude: float,
) -> AblationRun:
    obs = MamaEventObserver(
        sink=MemoryNdjsonSink(),
        binding_sink=MemoryNdjsonSink(),
        self_state_sink=MemoryNdjsonSink(),
        use_self_state=config.use_self_state,
        use_caregiver=config.use_caregiver,
        use_consolidation=config.use_consolidation,
        use_voice=config.use_voice,
        use_multimodal=config.use_multimodal,
    )
    obs.note_stress(magnitude=stress_magnitude)

    totals: list[int] = []
    for evt in events:
        result = obs.observe(evt)
        totals.append(result.breakdown.total)

    top_strength = 0.0
    if config.use_caregiver:
        rank = obs.binding_tracker.rank(top_n=1)
        if rank:
            top_strength = rank[0].strength

    return AblationRun(
        config=config,
        score_totals=totals,
        band_counts=obs.band_counts(),
        verdict=obs.gate_verdict().value,
        preferred_caregiver=(
            obs.episodic_store.preferred_caregiver()
            if config.use_consolidation
            else None
        ),
        top_binding_strength=top_strength,
        event_count=len(events),
    )
