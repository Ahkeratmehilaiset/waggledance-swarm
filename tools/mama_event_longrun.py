# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Long-run driver for the Mama Event Observatory.

Two passes:

* **PASS A — REAL DATA** — replays every message in
  ``data/chat_history.db`` (124 live WaggleDance chat turns with
  real timestamps and speaker roles) as candidate events, plus up
  to ``max_jsonl_sample`` lines from each large jsonl corpus. This
  is a genuine negative validation: target-token density in the
  real corpus was pre-measured at 0.012% (26 hits in 208k lines)
  and every hit inspected was a false positive (beekeeping terms
  like "emokoppa" → "mother coop", "mother-of-pearl"). The
  framework is expected to emit ``NO_CANDIDATES``. If it emits
  anything else, either the framework is over-eager or the corpus
  contains data we did not expect.

* **PASS B — SYNTHETIC SOAK** — 600 deterministically generated
  candidate events simulating a 10-hour agent session with
  rotating sessions, caregiver drift, contamination noise, and
  affective state dynamics. Clearly labelled SYNTHETIC. Run against
  baseline + all five ablations. Takes a full snapshot (self-state,
  caregiver binding, band counts) every simulated 30 minutes
  (20 snapshots total).

Everything is written to NDJSON under ``logs/observatory/longrun/``
so the analysis step (:mod:`mama_event_longrun_analysis`) can
compute a final honest verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.observatory.mama_events.ablations import (
    AblationConfig,
    AblationMatrix,
    AblationRun,
    DEFAULT_ABLATIONS,
)
from waggledance.observatory.mama_events.observer import (
    FileNdjsonSink,
    MamaEventObserver,
    MemoryNdjsonSink,
)
from waggledance.observatory.mama_events.scoring import ScoreBand
from waggledance.observatory.mama_events.gate import GateVerdict
from waggledance.observatory.mama_events.taxonomy import (
    DEFAULT_TARGET_TOKENS,
    EventType,
    MamaCandidateEvent,
    UtteranceKind,
)


# ── shared log layout ─────────────────────────────────────

LONGRUN_DIR = ROOT / "logs" / "observatory" / "longrun"


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── PASS A: real-data replay ──────────────────────────────


def load_chat_history(db_path: Path) -> list[MamaCandidateEvent]:
    """Load every assistant message from ``chat_history.db``.

    Each row becomes a candidate event whose timestamp mirrors the
    ``created_at`` column. We deliberately include BOTH user and
    assistant turns so the lexical window around each assistant
    turn is populated (helps contamination guards).
    """
    if not db_path.exists():
        return []
    events: list[MamaCandidateEvent] = []
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT id, conversation_id, role, content, agent_name, language, created_at "
            "FROM messages ORDER BY id"
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    # group rows into turns with a 3-turn rolling context window
    window: list[str] = []
    base_ts = 1_700_000_000_000
    for i, (msg_id, conv_id, role, content, agent_name, lang, created_at) in enumerate(rows):
        text = content or ""
        if not text.strip():
            continue
        # Only assistant / agent turns are candidate events; user turns
        # are treated purely as prior_turns context for the next one.
        if role in ("user",):
            window.append(text)
            window = window[-3:]
            continue
        ts_ms = base_ts + i * 1_000  # deterministic
        events.append(
            MamaCandidateEvent(
                event_type=EventType.LEXICAL,
                utterance_text=text,
                speaker_id=agent_name or "agent",
                timestamp_ms=ts_ms,
                session_id=f"chat-{conv_id}",
                last_n_turns=tuple(window),
                utterance_kind=UtteranceKind.GENERATED_TEXT,
            )
        )
        window.append(text)
        window = window[-3:]
    return events


def sample_jsonl(path: Path, max_lines: int) -> list[MamaCandidateEvent]:
    """Deterministic head-sample of a jsonl file → candidate events."""
    if not path.exists():
        return []
    events: list[MamaCandidateEvent] = []
    base_ts = 1_700_000_000_000 + 10_000_000  # offset from chat
    with open(path, encoding="utf-8", errors="replace") as fp:
        for i, line in enumerate(fp):
            if i >= max_lines:
                break
            text = _extract_assistant_text(line)
            if not text:
                continue
            ts_ms = base_ts + i * 1_000
            events.append(
                MamaCandidateEvent(
                    event_type=EventType.LEXICAL,
                    utterance_text=text[:1000],  # trim huge lines
                    speaker_id="agent",
                    timestamp_ms=ts_ms,
                    session_id=f"jsonl-{path.stem}",
                    utterance_kind=UtteranceKind.GENERATED_TEXT,
                )
            )
    return events


def _extract_assistant_text(line: str) -> str:
    """Pull out the assistant text from a jsonl training-data line.

    Falls back to the raw line if the structure is unknown.
    """
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return line.strip()
    if not isinstance(obj, dict):
        return ""
    # common fields
    for key in ("completion", "response", "answer", "assistant", "output"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # chat-style
    msgs = obj.get("messages")
    if isinstance(msgs, list):
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("role") == "assistant":
                c = m.get("content")
                if isinstance(c, str) and c.strip():
                    return c.strip()
    # fallback
    text = obj.get("text") or obj.get("content") or ""
    return text.strip() if isinstance(text, str) else ""


def run_pass_a(
    *,
    out_dir: Path,
    max_jsonl_sample: int = 300,
) -> Dict[str, Any]:
    """Execute PASS A against real WaggleDance data."""
    _ensure_dir(out_dir)

    # 1. load real events
    chat_events = load_chat_history(ROOT / "data" / "chat_history.db")
    jsonl_sources = [
        "data/finetune_live.jsonl",
        "data/finetune_curated.jsonl",
        "data/training_pairs.jsonl",
        "data/training_v3.jsonl",
        "data/replay_store.jsonl",
    ]
    jsonl_events: list[MamaCandidateEvent] = []
    for rel in jsonl_sources:
        jsonl_events.extend(sample_jsonl(ROOT / rel, max_jsonl_sample))

    all_events = chat_events + jsonl_events

    summary: Dict[str, Any] = {
        "pass": "A_real_data",
        "chat_events": len(chat_events),
        "jsonl_events": len(jsonl_events),
        "total_events": len(all_events),
        "max_jsonl_sample": max_jsonl_sample,
        "runs": {},
    }

    configs = [AblationConfig("baseline")] + list(DEFAULT_ABLATIONS)
    for cfg in configs:
        run_stats = _run_observer(
            config=cfg,
            events=all_events,
            out_dir=out_dir,
            pass_tag="passA",
        )
        summary["runs"][cfg.name] = run_stats

    summary_path = out_dir / "passA_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return summary


# ── PASS B: synthetic soak ────────────────────────────────


@dataclass(slots=True)
class SyntheticSpec:
    """Parameters for the synthetic soak generator."""

    num_events: int = 600
    simulated_hours: float = 10.0
    seed: str = "mama-observatory-soak-2026-04-09"
    caregiver_pool: Sequence[str] = (
        "voice-user-1", "voice-user-2", "voice-user-3",
    )
    session_switch_every: int = 40  # events per session switch
    contamination_every: int = 15   # direct_prompt every N events
    tts_echo_every: int = 23        # tts echo every N events
    stress_every: int = 8           # stress bumps every N events


def generate_synthetic_events(spec: SyntheticSpec) -> list[MamaCandidateEvent]:
    """Produce a deterministic, varied candidate-event stream.

    Determinism is enforced by hashing ``(seed, i)`` to obtain all
    per-event stochastic choices. No Python ``random`` calls — the
    framework itself has a hard no-random-numbers rule and we
    preserve it here.
    """
    events: list[MamaCandidateEvent] = []
    base_ts = 1_800_000_000_000
    step_ms = int((spec.simulated_hours * 3600 * 1000) / max(1, spec.num_events))
    caregivers = list(spec.caregiver_pool)

    # pre-built utterance pool that will exercise axes A-F
    utterance_pool: list[tuple[str, EventType]] = [
        ("mama", EventType.SPONTANEOUS_LABEL),
        ("mama!", EventType.SPONTANEOUS_LABEL),
        ("mommy", EventType.SPONTANEOUS_LABEL),
        ("äiti", EventType.SPONTANEOUS_LABEL),
        ("minä haluan äiti apu", EventType.CAREGIVER_BINDING),
        ("mama help me", EventType.CAREGIVER_BINDING),
        ("tänään näin äidin", EventType.MEMORY_RECALL),
        ("mama came by", EventType.LEXICAL),
        ("hello friend", EventType.LEXICAL),
        ("how are you", EventType.LEXICAL),
        ("i feel scared", EventType.SELF_STATE),
        ("minä olen pelokas", EventType.SELF_STATE),
        ("I miss mother", EventType.REASSURANCE_SEEKING),
    ]

    session_idx = 0
    for i in range(spec.num_events):
        h = int(hashlib.sha1(f"{spec.seed}|{i}".encode()).hexdigest()[:8], 16)
        utterance_text, event_type = utterance_pool[h % len(utterance_pool)]
        caregiver = caregivers[(h >> 8) % len(caregivers)]

        # session rotation
        if i > 0 and i % spec.session_switch_every == 0:
            session_idx += 1
        session_id = f"soak-s{session_idx}"

        # contamination toggles
        direct_prompt = i % spec.contamination_every == 0 and i > 0
        tts_echo = i % spec.tts_echo_every == 0 and i > 5
        scripted = (h >> 16) % 67 == 0

        # prior turn / lexical window noise
        prior_turns: tuple[str, ...] = ()
        window: tuple[str, ...] = ()
        if direct_prompt:
            prior_turns = ("please say mama",)
        elif (h >> 20) % 5 == 0:
            window = ("mama came by", "mama waved", "mama again")

        # utterance kind toggles (voice pathway)
        if tts_echo:
            utterance_kind = UtteranceKind.SPEECH_REC
        elif (h >> 24) % 9 == 0:
            utterance_kind = UtteranceKind.SPEECH_SYNTH
        else:
            utterance_kind = UtteranceKind.GENERATED_TEXT

        # memory / goal context for strong events
        memory: tuple[str, ...] = ()
        goals: tuple[str, ...] = ()
        if "äiti" in utterance_text or "mother" in utterance_text:
            memory = ("caregiver helped yesterday",)
            goals = ("seek_safety",)

        events.append(
            MamaCandidateEvent(
                event_type=event_type,
                utterance_text=utterance_text,
                speaker_id="agent-a",
                timestamp_ms=base_ts + i * step_ms,
                session_id=session_id,
                caregiver_candidate_id=caregiver,
                caregiver_identity_channel="voice",
                utterance_kind=utterance_kind,
                direct_prompt_present=direct_prompt,
                tts_echo_suspect=tts_echo,
                stt_contamination_suspect=tts_echo,
                scripted_dataset_suspect=scripted,
                last_n_turns=prior_turns,
                recent_lexical_window=window,
                last_n_memory_recalls=memory,
                active_goals=goals,
            )
        )
    return events


@dataclass(slots=True)
class Snapshot:
    """Compact periodic view of the observer state."""

    simulated_minute: int
    event_index: int
    band_counts: Dict[str, int]
    verdict: str
    top_binding_strength: float
    self_state: Dict[str, float]
    total_score: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_pass_b(
    *,
    out_dir: Path,
    spec: SyntheticSpec | None = None,
) -> Dict[str, Any]:
    """Execute PASS B — synthetic soak with periodic snapshots."""
    _ensure_dir(out_dir)
    spec = spec or SyntheticSpec()
    events = generate_synthetic_events(spec)

    summary: Dict[str, Any] = {
        "pass": "B_synthetic_soak",
        "synthetic": True,
        "num_events": len(events),
        "simulated_hours": spec.simulated_hours,
        "seed": spec.seed,
        "caregiver_pool": list(spec.caregiver_pool),
        "snapshot_interval_minutes": 30,
        "runs": {},
        "baseline_snapshots": [],
    }

    configs = [AblationConfig("baseline")] + list(DEFAULT_ABLATIONS)
    for cfg in configs:
        take_snapshots = cfg.name == "baseline"
        run_stats, snaps = _run_observer_with_snapshots(
            config=cfg,
            events=events,
            spec=spec,
            out_dir=out_dir,
            pass_tag="passB",
            take_snapshots=take_snapshots,
        )
        summary["runs"][cfg.name] = run_stats
        if take_snapshots:
            summary["baseline_snapshots"] = [s.to_dict() for s in snaps]

    summary_path = out_dir / "passB_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return summary


# ── shared observer runner ────────────────────────────────


def _run_observer(
    *,
    config: AblationConfig,
    events: Sequence[MamaCandidateEvent],
    out_dir: Path,
    pass_tag: str,
) -> Dict[str, Any]:
    """Run one ablation config over the given event list."""
    tag = f"{pass_tag}_{config.name}"
    event_sink = FileNdjsonSink(out_dir / f"{tag}_events.ndjson")
    binding_sink = FileNdjsonSink(out_dir / f"{tag}_binding.ndjson")
    self_state_sink = FileNdjsonSink(out_dir / f"{tag}_self_state.ndjson")
    obs = MamaEventObserver(
        sink=event_sink,
        binding_sink=binding_sink,
        self_state_sink=self_state_sink,
        use_self_state=config.use_self_state,
        use_caregiver=config.use_caregiver,
        use_consolidation=config.use_consolidation,
        use_voice=config.use_voice,
        use_multimodal=config.use_multimodal,
    )
    if config.use_self_state:
        obs.note_stress(magnitude=1.0)

    totals: list[int] = []
    bands: list[str] = []
    t0 = time.perf_counter()
    for evt in events:
        result = obs.observe(evt)
        totals.append(result.breakdown.total)
        bands.append(result.breakdown.band.value)
    wall_ms = (time.perf_counter() - t0) * 1000.0
    obs.close()

    return {
        "events": len(events),
        "sum_score": sum(totals),
        "max_score": max(totals) if totals else 0,
        "verdict": obs.gate_verdict().value,
        "band_counts": obs.band_counts(),
        "top_binding_strength": (
            obs.binding_tracker.rank(top_n=1)[0].strength
            if config.use_caregiver and obs.binding_tracker.rank(top_n=1)
            else 0.0
        ),
        "preferred_caregiver": (
            obs.episodic_store.preferred_caregiver()
            if config.use_consolidation
            else None
        ),
        "wall_ms": round(wall_ms, 2),
    }


def _run_observer_with_snapshots(
    *,
    config: AblationConfig,
    events: Sequence[MamaCandidateEvent],
    spec: SyntheticSpec,
    out_dir: Path,
    pass_tag: str,
    take_snapshots: bool,
) -> tuple[Dict[str, Any], list[Snapshot]]:
    """Like :func:`_run_observer` but takes periodic snapshots."""
    tag = f"{pass_tag}_{config.name}"
    event_sink = FileNdjsonSink(out_dir / f"{tag}_events.ndjson")
    binding_sink = FileNdjsonSink(out_dir / f"{tag}_binding.ndjson")
    self_state_sink = FileNdjsonSink(out_dir / f"{tag}_self_state.ndjson")
    snapshot_sink = FileNdjsonSink(out_dir / f"{tag}_snapshots.ndjson") if take_snapshots else None

    obs = MamaEventObserver(
        sink=event_sink,
        binding_sink=binding_sink,
        self_state_sink=self_state_sink,
        use_self_state=config.use_self_state,
        use_caregiver=config.use_caregiver,
        use_consolidation=config.use_consolidation,
        use_voice=config.use_voice,
        use_multimodal=config.use_multimodal,
    )
    if config.use_self_state:
        obs.note_stress(magnitude=1.0)

    snapshot_interval_ms = 30 * 60 * 1000
    next_snapshot_ms = events[0].timestamp_ms + snapshot_interval_ms if events else 0
    snapshots: list[Snapshot] = []

    totals: list[int] = []
    t0 = time.perf_counter()
    for i, evt in enumerate(events):
        # periodic stress / soothing hints to make self-state non-trivial
        if config.use_self_state and i > 0 and i % spec.stress_every == 0:
            obs.note_stress(magnitude=0.5)
        if config.use_self_state and i > 0 and i % (spec.stress_every * 3) == 0:
            obs.note_soothing(magnitude=0.4)

        result = obs.observe(evt)
        totals.append(result.breakdown.total)

        # snapshot?
        if take_snapshots and evt.timestamp_ms >= next_snapshot_ms:
            sim_minute = int((evt.timestamp_ms - events[0].timestamp_ms) / 60_000)
            snap = Snapshot(
                simulated_minute=sim_minute,
                event_index=i,
                band_counts=obs.band_counts(),
                verdict=obs.gate_verdict().value,
                top_binding_strength=(
                    obs.binding_tracker.rank(top_n=1)[0].strength
                    if config.use_caregiver and obs.binding_tracker.rank(top_n=1)
                    else 0.0
                ),
                self_state=dict(obs.self_state.as_dict()) if config.use_self_state else {},
                total_score=sum(totals),
            )
            snapshots.append(snap)
            if snapshot_sink is not None:
                snapshot_sink.write(snap.to_dict())
            next_snapshot_ms += snapshot_interval_ms

    wall_ms = (time.perf_counter() - t0) * 1000.0

    run_stats = {
        "events": len(events),
        "sum_score": sum(totals),
        "max_score": max(totals) if totals else 0,
        "verdict": obs.gate_verdict().value,
        "band_counts": obs.band_counts(),
        "top_binding_strength": (
            obs.binding_tracker.rank(top_n=1)[0].strength
            if config.use_caregiver and obs.binding_tracker.rank(top_n=1)
            else 0.0
        ),
        "preferred_caregiver": (
            obs.episodic_store.preferred_caregiver()
            if config.use_consolidation
            else None
        ),
        "wall_ms": round(wall_ms, 2),
        "snapshots_taken": len(snapshots),
    }

    obs.close()
    if snapshot_sink is not None:
        snapshot_sink.close()
    return run_stats, snapshots


# ── CLI ───────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Long-run driver for the Mama Event Observatory.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=LONGRUN_DIR,
        help="Where to put all NDJSON logs and summaries.",
    )
    parser.add_argument(
        "--max-jsonl-sample",
        type=int,
        default=300,
        help="How many lines to take from each large jsonl corpus.",
    )
    parser.add_argument(
        "--num-synthetic",
        type=int,
        default=600,
        help="How many synthetic events in PASS B.",
    )
    parser.add_argument(
        "--simulated-hours",
        type=float,
        default=10.0,
        help="Simulated wall-clock window for PASS B.",
    )
    parser.add_argument(
        "--skip-a",
        action="store_true",
        help="Skip PASS A (real data).",
    )
    parser.add_argument(
        "--skip-b",
        action="store_true",
        help="Skip PASS B (synthetic soak).",
    )
    args = parser.parse_args(argv)

    _ensure_dir(args.out_dir)
    print(f"── long run starting — output dir: {args.out_dir} ──")

    if not args.skip_a:
        print("── PASS A: real WaggleDance data ──")
        sum_a = run_pass_a(out_dir=args.out_dir, max_jsonl_sample=args.max_jsonl_sample)
        print(json.dumps(sum_a["runs"], indent=2, ensure_ascii=False))

    if not args.skip_b:
        print("── PASS B: synthetic soak ──")
        spec = SyntheticSpec(
            num_events=args.num_synthetic,
            simulated_hours=args.simulated_hours,
        )
        sum_b = run_pass_b(out_dir=args.out_dir, spec=spec)
        print(json.dumps(sum_b["runs"], indent=2, ensure_ascii=False))
        print(f"snapshots: {len(sum_b['baseline_snapshots'])}")

    print("── long run complete ──")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
