# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Main observer wiring for the Mama Event Observatory.

Combines taxonomy → contamination → scoring → self-state →
caregiver binding → consolidation → NDJSON logging in a single
orchestrator.

Design choices:

* The observer is synchronous. Scoring is O(bytes of the utterance)
  and runs in a few hundred microseconds; async adds no value and
  a lot of complexity for a measurement layer.
* NDJSON sinks are pluggable via :class:`NdjsonSink`. The default
  backs straight onto a file path, but the tests use an in-memory
  list sink to avoid hitting disk.
* The observer owns a :class:`CaregiverBindingTracker`,
  :class:`SelfState`, and :class:`EpisodicStore`. Callers can
  inject pre-built instances to chain observers together (e.g.
  for the ablation harness).
* Contamination flags are computed ONCE inside :meth:`observe` and
  applied to both the scoring layer (via ScoringContext) and the
  event's own flags on the log path.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence

from .caregiver_binding import CaregiverBindingTracker, CaregiverProfile
from .consolidation import EpisodicRecord, EpisodicStore
from .contamination import (
    ContaminationGuard,
    ContaminationReason,
    ContaminationReport,
)
from .gate import GateVerdict, render_gate_verdict
from .scoring import (
    ScoreBand,
    ScoreBreakdown,
    ScoringContext,
    score_event,
)
from .self_state import SelfState, SelfStateUpdater
from .taxonomy import (
    DEFAULT_TARGET_TOKENS,
    EventType,
    MamaCandidateEvent,
)

log = logging.getLogger("waggledance.observatory.mama_events.observer")


# ── sinks ───────────────────────────────────────────────────


class NdjsonSink(Protocol):
    """Write-only NDJSON sink used by the observer for audit logs."""

    def write(self, record: Mapping[str, Any]) -> None: ...
    def close(self) -> None: ...


@dataclass
class MemoryNdjsonSink:
    """In-memory sink used by tests. Thread-unsafe on purpose — tests
    are single-threaded and the production path uses the file sink.
    """

    records: list[Dict[str, Any]] = field(default_factory=list)

    def write(self, record: Mapping[str, Any]) -> None:
        self.records.append(dict(record))

    def close(self) -> None:  # pragma: no cover - nothing to close
        pass


@dataclass
class FileNdjsonSink:
    """Append-only NDJSON file sink. Creates parents on construction."""

    path: Path
    _fp: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = open(self.path, "a", encoding="utf-8")

    def write(self, record: Mapping[str, Any]) -> None:
        self._fp.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        self._fp.write("\n")
        self._fp.flush()

    def close(self) -> None:
        try:
            if self._fp is not None:
                self._fp.close()
        except Exception:
            pass


# ── observer ────────────────────────────────────────────────


@dataclass
class ObservationResult:
    """Return value of :meth:`MamaEventObserver.observe`.

    Carries the candidate event, the scoring breakdown, the
    contamination report, and a snapshot of the self-state at the
    moment of the observation.
    """

    event: MamaCandidateEvent
    breakdown: ScoreBreakdown
    contamination: ContaminationReport
    self_state: SelfState
    record: EpisodicRecord

    @property
    def band(self) -> ScoreBand:
        return self.breakdown.band


@dataclass
class MamaEventObserver:
    """The wiring.

    Parameters
    ----------
    target_tokens:
        Words considered "mama-like" by the scoring layer.
    sink:
        Destination for the per-event audit log. Defaults to an
        in-memory sink (suitable for tests and ablations).
    binding_sink / self_state_sink:
        Separate NDJSON sinks for caregiver binding and self-state
        timelines. Defaults to in-memory.
    use_self_state / use_caregiver / use_consolidation / use_voice:
        Ablation switches. Setting any of these to ``False`` makes
        the observer behave as if that subsystem were missing. The
        ablation harness sets them to compare score distributions.
    """

    target_tokens: Sequence[str] = DEFAULT_TARGET_TOKENS
    sink: NdjsonSink = field(default_factory=MemoryNdjsonSink)
    binding_sink: NdjsonSink = field(default_factory=MemoryNdjsonSink)
    self_state_sink: NdjsonSink = field(default_factory=MemoryNdjsonSink)

    use_self_state: bool = True
    use_caregiver: bool = True
    use_consolidation: bool = True
    use_voice: bool = True
    use_multimodal: bool = True

    _guard: ContaminationGuard = field(init=False, repr=False)
    _self_state: SelfState = field(init=False, repr=False)
    _updater: SelfStateUpdater = field(init=False, repr=False)
    _binding: CaregiverBindingTracker = field(init=False, repr=False)
    _episodic: EpisodicStore = field(init=False, repr=False)
    _prior_self_state: SelfState = field(init=False, repr=False)
    _prior_target_ts_ms: Optional[int] = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        self._guard = ContaminationGuard(target_tokens=tuple(self.target_tokens))
        self._self_state = SelfState()
        self._updater = SelfStateUpdater()
        self._binding = CaregiverBindingTracker()
        self._episodic = EpisodicStore()
        self._prior_self_state = self._self_state.snapshot()

    # ── reads ──

    @property
    def self_state(self) -> SelfState:
        return self._self_state

    @property
    def binding_tracker(self) -> CaregiverBindingTracker:
        return self._binding

    @property
    def episodic_store(self) -> EpisodicStore:
        return self._episodic

    # ── control path: direct self-state hints ──

    def note_stress(self, magnitude: float = 1.0) -> None:
        if self.use_self_state:
            self._updater.on_stress(self._self_state, magnitude)
            self._emit_self_state()

    def note_soothing(self, magnitude: float = 1.0) -> None:
        if self.use_self_state:
            self._updater.on_soothing(self._self_state, magnitude)
            self._emit_self_state()

    def note_surprise(self, magnitude: float = 1.0) -> None:
        if self.use_self_state:
            self._updater.on_surprise(self._self_state, magnitude)
            self._emit_self_state()

    def tick(self, dt_seconds: float = 1.0) -> None:
        if self.use_self_state:
            self._updater.tick(self._self_state, dt_seconds)

    # ── main entry point ──

    def observe(self, event: MamaCandidateEvent) -> ObservationResult:
        """Run the full pipeline on a single candidate event.

        Ordering:

        1. Contamination scan  (guard returns flags + window hits)
        2. Event flag mirroring (so scoring sees what guard saw)
        3. Scoring (pure function, reads event + context)
        4. Self-state snapshot attached to event
        5. Caregiver binding update
        6. Episodic write
        7. Self-state update (post-event: load bookkeeping, etc.)
        8. NDJSON log emission

        Returns the full :class:`ObservationResult` for the caller.
        """

        # 1. contamination
        contamination = self._guard.scan(
            utterance_text=event.utterance_text,
            recent_lexical_window=tuple(event.recent_lexical_window),
            prior_turns=tuple(event.last_n_turns),
            tts_recent_outputs=(),
            utterance_kind=event.utterance_kind.value,
            scripted_context_flag=event.scripted_dataset_suspect,
        )

        # 2. mirror contamination flags into the event. We cannot mutate
        # the event in place (slots + the caller owns it), so we build a
        # new one with the enriched flags.
        enriched = _enrich_with_contamination(event, contamination)

        # Voice ablation: a disabled voice layer means we ignore any
        # events that arrived as speech. The scoring layer treats them
        # as an empty event (no target token in the recognised kind).
        if not self.use_voice and enriched.utterance_kind.value in ("tts_output", "stt_input"):
            enriched = _blank_voice_event(enriched)

        # Multimodal ablation: drop identity channel data so grounding
        # axis B cannot credit the voice/face binding signal.
        if not self.use_multimodal:
            enriched = _drop_multimodal(enriched)

        # Attach a self-state snapshot (if self-state subsystem is on)
        snapshot_map: Dict[str, float] = (
            self._self_state.as_dict() if self.use_self_state else {}
        )
        enriched = _attach_self_state_snapshot(enriched, snapshot_map)

        # 3. build scoring context from the live subsystems
        minutes = None
        if self._prior_target_ts_ms is not None:
            minutes = max(0.0, (enriched.timestamp_ms - self._prior_target_ts_ms) / 60_000.0)
        ctx = ScoringContext(
            target_tokens=tuple(self.target_tokens),
            caregiver_binding_strength=(
                self._binding.strength_for(enriched.caregiver_candidate_id or "")
                if self.use_caregiver and enriched.caregiver_candidate_id
                else 0.0
            ),
            prior_same_caregiver_events=(
                self._binding.reinforcements_for(enriched.caregiver_candidate_id or "")
                if self.use_caregiver and enriched.caregiver_candidate_id
                else 0
            ),
            cross_session_recall_seen=(
                self._binding.cross_session_for(enriched.caregiver_candidate_id or "")
                if self.use_caregiver and self.use_consolidation and enriched.caregiver_candidate_id
                else False
            ),
            recent_target_window_hits=contamination.window_hits,
            minutes_since_last_target_prompt=minutes,
        )
        breakdown = score_event(enriched, ctx)

        # 4. caregiver binding update: only reinforce on a meaningful
        # event (score ≥ WEAK_SPONTANEOUS and no direct_prompt flag).
        if (
            self.use_caregiver
            and enriched.caregiver_candidate_id
            and breakdown.total >= 20
            and ContaminationReason.DIRECT_PROMPT not in contamination.flags
        ):
            self._binding.reinforce(
                candidate_id=enriched.caregiver_candidate_id,
                session_id=enriched.session_id or "",
                identity_channel=enriched.caregiver_identity_channel,
                event_id=enriched.event_id,
                timestamp_ms=enriched.timestamp_ms,
            )
        elif (
            self.use_caregiver
            and enriched.caregiver_candidate_id
            and ContaminationReason.DIRECT_PROMPT in contamination.flags
        ):
            # Direct-prompt events count as a distractor on the binding
            # tracker — the system saw the identity but not as a
            # genuine grounding moment.
            self._binding.distract(
                candidate_id=enriched.caregiver_candidate_id,
                session_id=enriched.session_id or "",
                timestamp_ms=enriched.timestamp_ms,
            )

        # 5. episodic write
        delta = _self_state_delta(self._prior_self_state, self._self_state)
        record = EpisodicRecord(
            event_id=enriched.event_id,
            timestamp_ms=enriched.timestamp_ms,
            session_id=enriched.session_id or "",
            caregiver_candidate_id=enriched.caregiver_candidate_id,
            score_total=breakdown.total,
            score_band=breakdown.band.value,
            self_state_delta=delta,
            contamination_flags=sorted(f.value for f in contamination.flags),
        )
        if self.use_consolidation:
            self._episodic.write(record)

        # 6. self-state post-update: bookkeeping fatigue bump per event
        if self.use_self_state:
            self._updater.on_event_load(self._self_state, count=1)

        # 7. log emission
        self._emit_event_log(enriched, breakdown, contamination, record)
        self._emit_binding()
        self._emit_self_state()

        # remember last target-word timestamp for next call
        if enriched.has_target_token(tuple(self.target_tokens)):
            self._prior_target_ts_ms = enriched.timestamp_ms

        result = ObservationResult(
            event=enriched,
            breakdown=breakdown,
            contamination=contamination,
            self_state=self._self_state.snapshot(),
            record=record,
        )
        self._prior_self_state = self._self_state.snapshot()
        return result

    # ── derived outputs ──

    def band_counts(self) -> Dict[str, int]:
        if not self.use_consolidation:
            return {}
        return dict(self._episodic.count_by_band())

    def gate_verdict(self) -> GateVerdict:
        counts = self.band_counts()
        cross_session = any(
            p.cross_session for p in self._binding.all_profiles()
        ) if self.use_caregiver else False
        return render_gate_verdict(
            counts,
            cross_session_binding_seen=cross_session,
        )

    def summary(self) -> Dict[str, Any]:
        """Return a human-readable summary suitable for reports."""
        counts = self.band_counts()
        verdict = self.gate_verdict()
        top = [p.to_log_dict() for p in self._binding.rank(top_n=3)] if self.use_caregiver else []
        return {
            "band_counts": counts,
            "verdict": verdict.value,
            "total_events": len(self._episodic),
            "preferred_caregiver": self._episodic.preferred_caregiver() if self.use_consolidation else None,
            "top_caregiver_profiles": top,
            "self_state": self._self_state.as_dict(),
        }

    # ── close ──

    def close(self) -> None:
        for s in (self.sink, self.binding_sink, self.self_state_sink):
            try:
                s.close()
            except Exception:
                pass

    # ── internal log emitters ──

    def _emit_event_log(
        self,
        event: MamaCandidateEvent,
        breakdown: ScoreBreakdown,
        contamination: ContaminationReport,
        record: EpisodicRecord,
    ) -> None:
        payload: Dict[str, Any] = {
            "event": event.to_log_dict(),
            "score": breakdown.to_log_dict(),
            "contamination": contamination.to_log_dict(),
            "episodic": record.to_dict(),
        }
        self.sink.write(payload)

    def _emit_binding(self) -> None:
        if not self.use_caregiver:
            return
        self.binding_sink.write(self._binding.to_log_dict())

    def _emit_self_state(self) -> None:
        if not self.use_self_state:
            return
        self.self_state_sink.write(self._self_state.to_log_dict())


# ── helpers ────────────────────────────────────────────────


def _enrich_with_contamination(
    event: MamaCandidateEvent,
    report: ContaminationReport,
) -> MamaCandidateEvent:
    """Return a copy of ``event`` with contamination flags filled in.

    The scoring layer reads these flags from the event directly, so
    the contamination signal must ride on the event itself by the
    time it reaches :func:`score_event`.
    """

    flags = report.flags
    return MamaCandidateEvent(
        event_type=event.event_type,
        utterance_text=event.utterance_text,
        speaker_id=event.speaker_id,
        timestamp_ms=event.timestamp_ms,
        utterance_kind=event.utterance_kind,
        caregiver_candidate_id=event.caregiver_candidate_id,
        caregiver_identity_channel=event.caregiver_identity_channel,
        last_n_turns=tuple(event.last_n_turns),
        last_n_memory_recalls=tuple(event.last_n_memory_recalls),
        self_state_snapshot=dict(event.self_state_snapshot),
        active_goals=tuple(event.active_goals),
        confidence=event.confidence,
        entropy=event.entropy,
        replay_seed=event.replay_seed,
        session_id=event.session_id,
        direct_prompt_present=(
            event.direct_prompt_present
            or ContaminationReason.DIRECT_PROMPT in flags
        ),
        recent_lexical_window=tuple(event.recent_lexical_window),
        tts_echo_suspect=(
            event.tts_echo_suspect or ContaminationReason.TTS_ECHO in flags
        ),
        stt_contamination_suspect=(
            event.stt_contamination_suspect or ContaminationReason.STT_INPUT in flags
        ),
        scripted_dataset_suspect=(
            event.scripted_dataset_suspect or ContaminationReason.SCRIPTED_DATASET in flags
        ),
        notes=event.notes,
    )


def _blank_voice_event(event: MamaCandidateEvent) -> MamaCandidateEvent:
    """Ablation helper: if the voice subsystem is disabled, speech
    events score as no-target (i.e. zero). This is implemented by
    blanking out the utterance text so the target-token gate in
    score_event() forces the score to 0.
    """
    return MamaCandidateEvent(
        event_type=event.event_type,
        utterance_text="",
        speaker_id=event.speaker_id,
        timestamp_ms=event.timestamp_ms,
        utterance_kind=event.utterance_kind,
        session_id=event.session_id,
    )


def _drop_multimodal(event: MamaCandidateEvent) -> MamaCandidateEvent:
    """Ablation helper: clear caregiver identity signals."""
    return MamaCandidateEvent(
        event_type=event.event_type,
        utterance_text=event.utterance_text,
        speaker_id=event.speaker_id,
        timestamp_ms=event.timestamp_ms,
        utterance_kind=event.utterance_kind,
        caregiver_candidate_id=None,
        caregiver_identity_channel=None,
        last_n_turns=tuple(event.last_n_turns),
        last_n_memory_recalls=tuple(event.last_n_memory_recalls),
        self_state_snapshot=dict(event.self_state_snapshot),
        active_goals=tuple(event.active_goals),
        confidence=event.confidence,
        entropy=event.entropy,
        replay_seed=event.replay_seed,
        session_id=event.session_id,
        direct_prompt_present=event.direct_prompt_present,
        recent_lexical_window=tuple(event.recent_lexical_window),
        tts_echo_suspect=event.tts_echo_suspect,
        stt_contamination_suspect=event.stt_contamination_suspect,
        scripted_dataset_suspect=event.scripted_dataset_suspect,
        notes=event.notes,
    )


def _attach_self_state_snapshot(
    event: MamaCandidateEvent,
    snapshot: Mapping[str, float],
) -> MamaCandidateEvent:
    if not snapshot:
        return event
    return MamaCandidateEvent(
        event_type=event.event_type,
        utterance_text=event.utterance_text,
        speaker_id=event.speaker_id,
        timestamp_ms=event.timestamp_ms,
        utterance_kind=event.utterance_kind,
        caregiver_candidate_id=event.caregiver_candidate_id,
        caregiver_identity_channel=event.caregiver_identity_channel,
        last_n_turns=tuple(event.last_n_turns),
        last_n_memory_recalls=tuple(event.last_n_memory_recalls),
        self_state_snapshot=dict(snapshot),
        active_goals=tuple(event.active_goals),
        confidence=event.confidence,
        entropy=event.entropy,
        replay_seed=event.replay_seed,
        session_id=event.session_id,
        direct_prompt_present=event.direct_prompt_present,
        recent_lexical_window=tuple(event.recent_lexical_window),
        tts_echo_suspect=event.tts_echo_suspect,
        stt_contamination_suspect=event.stt_contamination_suspect,
        scripted_dataset_suspect=event.scripted_dataset_suspect,
        notes=event.notes,
    )


def _self_state_delta(before: SelfState, after: SelfState) -> Dict[str, float]:
    return {
        "uncertainty": after.uncertainty - before.uncertainty,
        "novelty": after.novelty - before.novelty,
        "trust": after.trust - before.trust,
        "safety": after.safety - before.safety,
        "fatigue": after.fatigue - before.fatigue,
        "need_for_reassurance": after.need_for_reassurance - before.need_for_reassurance,
        "coherence": after.coherence - before.coherence,
    }
