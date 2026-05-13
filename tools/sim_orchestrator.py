#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Replay bridge events through Approach-B dev-orchestrator.

This is the retrospective companion to tools/build_agent_flight_plan.py.
It scores the same bridge history that the Flight Plan builder consumes
prospectively, using the same formal status enum and the same metric
field names (``metrics`` block + ``formal_statuses`` block). One shared
vocabulary, two perspectives:

* ``build_agent_flight_plan.py`` -- prospective: "what should the next
  agent session do given the current bridge state?"
* ``sim_orchestrator.py``       -- retrospective: "did the past N hours
  of bridge state satisfy the coordination contract?"

The two reports are aligned per
``codex-orchestrator-sim-b-opinion-2026-05-12 / consensus_accepted`` so
their ``metrics`` blocks can be diffed directly without translation.

Read-only -- does not write to events.jsonl, does not modify state.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


REPO = Path(__file__).resolve().parent.parent
EVENTS_PATH = REPO / ".agent-bridge" / "shared" / "events.jsonl"

SCHEMA_VERSION = "agent-flight-plan-retrospective-v1"
SCHEMA_ALIGNED_WITH = "agent-flight-plan-v1"


# --- Flight Plan helper import --------------------------------------------
#
# Reuse the exact helpers the Flight Plan builder uses, so the formal
# status detection (``_is_consensus``, ``_has_rco``, ``_load_statuses``)
# behaves identically on both the prospective and retrospective sides.
# If a helper drifts on Codex's side, this importer follows automatically.

def _load_module(path: Path, module_name: str) -> Any | None:
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BUILDER_MODULE = _load_module(
    REPO / "tools" / "build_agent_flight_plan.py", "_afp_builder"
)

# Bridge classifier (regression-class triage) lives under .orchestrator/.
_CLASSIFIER_MODULE = _load_module(
    REPO / ".orchestrator" / "bridge_classify.py", "_bridge_classify"
)
classify = getattr(_CLASSIFIER_MODULE, "classify", None) if _CLASSIFIER_MODULE else None


def _load_statuses() -> dict[str, str]:
    if _BUILDER_MODULE is not None and hasattr(_BUILDER_MODULE, "_load_statuses"):
        return _BUILDER_MODULE._load_statuses()  # type: ignore[attr-defined]
    return {
        "rco_requested": "rco_requested",
        "rco_done": "rco_done",
        "consensus_proposal": "consensus_proposal",
        "consensus_accepted": "consensus_accepted",
        "claim_required": "claim_required",
        "missing_claim": "missing_claim",
    }


def _is_consensus(event: dict, statuses: dict[str, str]) -> bool:
    if _BUILDER_MODULE is not None and hasattr(_BUILDER_MODULE, "_is_consensus"):
        return bool(_BUILDER_MODULE._is_consensus(event, statuses))  # type: ignore[attr-defined]
    status = str(event.get("status") or "").lower()
    return status in {statuses["consensus_proposal"], statuses["consensus_accepted"]} \
        or status.startswith("consensus_")


def _has_rco(event: dict, statuses: dict[str, str]) -> bool:
    if _BUILDER_MODULE is not None and hasattr(_BUILDER_MODULE, "_has_rco"):
        return bool(_BUILDER_MODULE._has_rco(event, statuses))  # type: ignore[attr-defined]
    text = (str(event.get("status") or "") + " " + str(event.get("message") or "")).lower()
    return statuses["rco_requested"] in text or statuses["rco_done"] in text or " rco " in f" {text} "


# --- Event model ----------------------------------------------------------

@dataclass
class Event:
    ts: datetime
    agent: str
    type: str
    task_id: str
    status: str
    message: str
    payload: dict
    raw: dict


@dataclass
class TaskThread:
    task_id: str
    first_agent: str
    first_ts: datetime
    claim_agent: str | None = None
    claim_ts: datetime | None = None
    participants: set[str] = field(default_factory=set)
    events: list[Event] = field(default_factory=list)


def parse_events(path: Path, since: datetime) -> list[Event]:
    out: list[Event] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_raw = d.get("ts_utc", "")
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts < since:
                continue
            out.append(Event(
                ts=ts,
                agent=str(d.get("agent", "")),
                type=str(d.get("type", "")),
                task_id=str(d.get("task_id", "")),
                status=str(d.get("status", "")),
                message=str(d.get("message", "")),
                payload=d.get("payload") if isinstance(d.get("payload"), dict) else {},
                raw=d,
            ))
    out.sort(key=lambda e: e.ts)
    return out


def build_threads(events: list[Event]) -> dict[str, TaskThread]:
    threads: dict[str, TaskThread] = {}
    for ev in events:
        if not ev.task_id:
            continue
        th = threads.get(ev.task_id)
        if th is None:
            th = TaskThread(
                task_id=ev.task_id,
                first_agent=ev.agent,
                first_ts=ev.ts,
            )
            threads[ev.task_id] = th
        th.participants.add(ev.agent)
        th.events.append(ev)
        if ev.type == "claim" and th.claim_agent is None:
            th.claim_agent = ev.agent
            th.claim_ts = ev.ts
    return threads


# --- Aligned metrics (Flight Plan v1 vocabulary) --------------------------

def compute_aligned_metrics(
    threads: dict[str, TaskThread],
    statuses: dict[str, str],
) -> dict:
    """Compute the same metric block the Flight Plan builder emits.

    Field names match ``agent_flight_plan.schema.json::metrics`` exactly so
    retrospective and prospective reports can be diffed directly.
    """
    task_total = 0
    with_claim = 0
    multi_agent = 0
    multi_agent_without_claim = 0
    formal_rco = 0
    consensus = 0
    for th in threads.values():
        non_system = {a for a in th.participants if a != "system"}
        if not non_system:
            continue
        task_total += 1
        if th.claim_agent:
            with_claim += 1
        if len(non_system) > 1:
            multi_agent += 1
            if not th.claim_agent:
                multi_agent_without_claim += 1
        if any(_has_rco(ev.raw, statuses) for ev in th.events):
            formal_rco += 1
        if any(_is_consensus(ev.raw, statuses) for ev in th.events):
            consensus += 1
    coverage_pct = round(100.0 * with_claim / task_total, 1) if task_total else 0.0
    return {
        "task_threads_total": task_total,
        "threads_with_claim": with_claim,
        "claim_coverage_pct": coverage_pct,
        "multi_agent_threads": multi_agent,
        "multi_agent_threads_without_claim": multi_agent_without_claim,
        "formal_rco_threads": formal_rco,
        "consensus_topics": consensus,
    }


# --- Retrospective extensions --------------------------------------------

HANDSHAKE_WINDOW_MIN = 15  # peer must claim or ack within 15 min of first work post


def measure_handshake_examples(threads: dict[str, TaskThread]) -> dict:
    """Surface concrete examples of multi-agent threads that ran without
    a claim handshake. The aggregate count lives in metrics; this block
    keeps the audit-trail-friendly samples."""
    claim_before_work = 0
    examples_no_handshake: list[str] = []
    for th in threads.values():
        non_system = {a for a in th.participants if a != "system"}
        if not non_system:
            continue
        if th.claim_agent and th.claim_ts and th.claim_ts <= th.first_ts + timedelta(seconds=60):
            claim_before_work += 1
        if len(non_system) > 1 and not th.claim_agent and len(examples_no_handshake) < 8:
            examples_no_handshake.append(th.task_id)
    return {
        "claim_before_or_with_first_work": claim_before_work,
        "examples_no_handshake": examples_no_handshake,
    }


# Heuristic: detect "independent convergence" -- same conceptual work,
# different task_ids, both agents post on the same theme within a short
# window. This is a Claude-lane retrospective lens; the Flight Plan
# does not need it for the prospective view.
CONVERGENCE_KEYWORDS = [
    (re.compile(r"\bai[- ]?(?:assisted|mentor)\b", re.I), "ai_mentor_bootstrap"),
    (re.compile(r"\bbootstrap[- ]?kit\b", re.I), "ai_mentor_bootstrap"),
    (re.compile(r"\bsolver[- ]?bootcamp\b", re.I), "ai_mentor_bootstrap"),
    (re.compile(r"\bl54[- ]?reframed\b", re.I), "l54_capability_factory"),
    (re.compile(r"\bcapability[- ]?factory\b", re.I), "l54_capability_factory"),
    (re.compile(r"\blazy[- ]?bind\b", re.I), "l54_capability_factory"),
    (re.compile(r"\bL33\b"), "l33"),
    (re.compile(r"\bmock\.patch\b"), "mock_patch"),
    (re.compile(r"\bgod[- ]?class\b", re.I), "l52_god_class"),
    (re.compile(r"\bdecomposition\b", re.I), "l52_god_class"),
]


def measure_convergence(events: list[Event]) -> dict:
    """Detect themes that both agents independently surfaced."""
    by_theme: dict[str, dict[str, list[Event]]] = defaultdict(lambda: defaultdict(list))
    for ev in events:
        text = f"{ev.message} {ev.task_id} {json.dumps(ev.payload, ensure_ascii=False)}"
        for pattern, theme in CONVERGENCE_KEYWORDS:
            if pattern.search(text):
                by_theme[theme][ev.agent].append(ev)
                break
    convergences: list[dict] = []
    for theme, by_agent in by_theme.items():
        if "claude" in by_agent and "codex" in by_agent:
            c_first = min(e.ts for e in by_agent["claude"])
            x_first = min(e.ts for e in by_agent["codex"])
            gap_min = abs((c_first - x_first).total_seconds()) / 60.0
            convergences.append({
                "theme": theme,
                "claude_first_post": c_first.isoformat(),
                "codex_first_post": x_first.isoformat(),
                "gap_minutes": round(gap_min, 1),
                "claude_event_count": len(by_agent["claude"]),
                "codex_event_count": len(by_agent["codex"]),
            })
    convergences.sort(key=lambda c: c["gap_minutes"])
    return {
        "convergent_themes": len(convergences),
        "themes": convergences,
    }


def measure_pr_lifecycle(events: list[Event]) -> dict:
    """For each PR mentioned in payloads, who opened, who reviewed, who merged."""
    opens: dict[int, list[Event]] = defaultdict(list)
    reviews: dict[int, set[str]] = defaultdict(set)
    merges: dict[int, list[Event]] = defaultdict(list)
    for ev in events:
        pr = ev.payload.get("pr") or ev.payload.get("pr_number") or ev.payload.get("pull_request")
        if not isinstance(pr, int):
            continue
        text = (ev.message or "").lower()
        s = ev.status.lower()
        if "pr_open" in s or s in ("opened", "pushed", "open"):
            opens[pr].append(ev)
        if any(k in text for k in ("rco", "review", "endorse", "approve")) or s in ("endorsed", "reviewed"):
            reviews[pr].add(ev.agent)
        if "merged" in s:
            merges[pr].append(ev)
    rco_coverage_hits = 0
    rco_coverage_total = 0
    missing_rco: list[int] = []
    for pr, open_evs in opens.items():
        if not open_evs:
            continue
        opener = open_evs[0].agent
        peer = "codex" if opener == "claude" else "claude" if opener == "codex" else None
        if peer is None:
            continue
        rco_coverage_total += 1
        if peer in reviews.get(pr, set()):
            rco_coverage_hits += 1
        else:
            missing_rco.append(pr)
    return {
        "prs_touched": len(opens),
        "prs_merged": len([p for p, m in merges.items() if m]),
        "rco_total": rco_coverage_total,
        "rco_with_peer_review": rco_coverage_hits,
        "rco_missing": sorted(missing_rco)[:20],
        "rco_coverage_pct": round(100.0 * rco_coverage_hits / max(1, rco_coverage_total), 1),
    }


def measure_finding_quality(events: list[Event]) -> dict:
    """Look at 'finding' type events and classify the false-positive ratio,
    using explicit retract / false-positive ack messages in the same thread."""
    findings: dict[str, list[Event]] = defaultdict(list)
    for ev in events:
        if ev.type == "finding":
            findings[ev.task_id or f"_{ev.ts}"].append(ev)
    retracted = 0
    total = 0
    fp_keywords = re.compile(
        r"\b(false[- ]?positive|retract|withdraw|correction|not[- ]?actually|"
        r"acknowledged.*not|disagree)\b", re.I)
    fp_examples: list[str] = []
    for tid, evs in findings.items():
        total += len(evs)
        for ev in evs:
            for peer_ev in events:
                if peer_ev.task_id == tid and peer_ev.ts > ev.ts and peer_ev.agent != ev.agent:
                    if fp_keywords.search(peer_ev.message or ""):
                        retracted += 1
                        if len(fp_examples) < 5:
                            fp_examples.append(f"{tid}: peer flagged via '{peer_ev.message[:80]}'")
                        break
    return {
        "total_findings": total,
        "retracted_or_disputed": retracted,
        "fp_rate_pct": round(100.0 * retracted / max(1, total), 1),
        "examples": fp_examples,
    }


def measure_classifier_coverage(events: list[Event]) -> dict:
    """Run .orchestrator/bridge_classify.py over events that mention failures."""
    if classify is None:
        return {"classifier_available": False}
    failure_evs = [
        e for e in events
        if "fail" in (e.message or "").lower()
        or "error" in (e.message or "").lower()
        or e.status.lower() in ("failed", "failure", "blocked")
    ]
    klass_counts: Counter = Counter()
    for ev in failure_evs:
        text = (ev.message or "") + " " + json.dumps(ev.payload, ensure_ascii=False)
        try:
            klass = classify(text)
            klass_counts[klass.value if hasattr(klass, "value") else str(klass)] += 1
        except Exception:
            klass_counts["_classifier_error"] += 1
    return {
        "classifier_available": True,
        "failure_events_seen": len(failure_evs),
        "classification_distribution": dict(klass_counts.most_common()),
    }


def measure_lane_balance(events: list[Event]) -> dict:
    """How were the lanes actually distributed?"""
    by_agent_type: dict[str, Counter] = defaultdict(Counter)
    by_agent_status: dict[str, Counter] = defaultdict(Counter)
    for ev in events:
        if ev.agent == "system":
            continue
        by_agent_type[ev.agent][ev.type] += 1
        if ev.status:
            by_agent_status[ev.agent][ev.status] += 1
    impl_keywords = re.compile(r"\b(impl|implement|capability_loader|registry|adapter|container)\b", re.I)
    substrate_keywords = re.compile(r"\b(adr|substrate|contract|invariant|index|docs)\b", re.I)
    impl_lane = Counter()
    substrate_lane = Counter()
    for ev in events:
        if ev.agent == "system":
            continue
        text = ev.message + " " + ev.task_id + " " + json.dumps(ev.payload, ensure_ascii=False)
        if impl_keywords.search(text):
            impl_lane[ev.agent] += 1
        if substrate_keywords.search(text):
            substrate_lane[ev.agent] += 1
    return {
        "events_per_agent_per_type": {a: dict(c.most_common(8)) for a, c in by_agent_type.items()},
        "impl_lane_mentions": dict(impl_lane),
        "substrate_lane_mentions": dict(substrate_lane),
    }


def project_approach_b_savings(
    metrics: dict,
    convergence: dict,
    pr: dict,
    findings: dict,
) -> dict:
    """Translate observed gaps into estimates of what Approach B would have caught.

    Reuses the aligned ``metrics`` block so field names line up with the
    prospective Flight Plan.
    """
    duplicated_themes_under_60min = sum(
        1 for c in convergence["themes"] if c["gap_minutes"] < 60.0
    )
    missing_rco = pr["rco_total"] - pr["rco_with_peer_review"]
    fp_caught_by_pre_rco = findings["retracted_or_disputed"]
    return {
        "duplicate_work_prevented_estimate": duplicated_themes_under_60min,
        "rco_gaps_to_close": missing_rco,
        "false_positive_findings_caught_pre_publish": fp_caught_by_pre_rco,
        "multi_agent_threads_without_claim": metrics["multi_agent_threads_without_claim"],
    }


# --- Report assembly ------------------------------------------------------

def build_report(
    events: list[Event],
    threads: dict[str, TaskThread],
    *,
    events_path: str,
    since_hours: float,
    cutoff: datetime,
    approach: str,
    statuses: dict[str, str] | None = None,
    now_utc: str | None = None,
) -> dict:
    statuses = statuses if statuses is not None else _load_statuses()
    metrics = compute_aligned_metrics(threads, statuses)
    convergence = measure_convergence(events)
    pr_lifecycle = measure_pr_lifecycle(events)
    finding_quality = measure_finding_quality(events)
    last_event_ts = events[-1].ts.isoformat() if events else ""
    return {
        "schema_version": SCHEMA_VERSION,
        "schema_aligned_with": SCHEMA_ALIGNED_WITH,
        "generated_at_utc": now_utc or datetime.now(timezone.utc).isoformat(),
        "approach": approach,
        "source": {
            "events_path": events_path,
            "event_count": len(events),
            "last_event_ts_utc": last_event_ts,
            "cutoff_utc": cutoff.isoformat(),
            "since_hours": since_hours,
        },
        "metrics": metrics,
        "formal_statuses": statuses,
        "retrospective_extensions": {
            "agents": dict(Counter(e.agent for e in events).most_common()),
            "handshake_examples": measure_handshake_examples(threads),
            "independent_convergences": convergence,
            "pr_lifecycle": pr_lifecycle,
            "finding_quality": finding_quality,
            "bridge_classify_coverage": measure_classifier_coverage(events),
            "lane_balance": measure_lane_balance(events),
            "approach_b_projection": project_approach_b_savings(
                metrics, convergence, pr_lifecycle, finding_quality
            ),
        },
    }


# --- Streaming mode (Sprint 1 wave 3 instrumentation) --------------------
#
# Adds a streaming-mode counterpart to the existing retrospective scanner.
# The retrospective mode reads the entire events.jsonl in one shot and
# computes metrics; the streaming mode maintains in-memory thread state
# and emits a metric snapshot at a configurable interval.
#
# Design spec:
# iterations/anchor_use_case/sprint_1/claude_lane/sim_orchestrator_runtime_instrumentation_spec.md
#
# The streaming additions live under guard helpers so they do not
# perturb the retrospective entry point.


STREAM_SCHEMA_VERSION = "agent-flight-plan-live-v1"


@dataclass
class StreamState:
    """In-memory metric state for incremental updates.

    Tracked: byte cursor into events.jsonl (so resumed runs do not
    re-scan from the start), per-task thread map (same shape as
    retrospective TaskThread), all-events list (for convergence /
    pr_lifecycle / finding_quality extensions if requested), last
    snapshot timestamp, snapshot counter.
    """

    cursor_bytes: int = 0
    threads: dict[str, TaskThread] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    last_snapshot_ts_utc: str | None = None
    snapshot_count: int = 0


def _parse_event_dict(d: dict) -> Event | None:
    ts_raw = d.get("ts_utc", "")
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return Event(
        ts=ts,
        agent=str(d.get("agent", "")),
        type=str(d.get("type", "")),
        task_id=str(d.get("task_id", "")),
        status=str(d.get("status", "")),
        message=str(d.get("message", "")),
        payload=d.get("payload") if isinstance(d.get("payload"), dict) else {},
        raw=d,
    )


def read_events_from_offset(
    path: Path, offset_bytes: int
) -> tuple[list[Event], int]:
    """Read events appended after offset_bytes; return (events, new_offset).

    Returns events in file order. If a line is partial (file mid-write),
    that line is not consumed and the offset stays just before it so the
    next call retries. The function never raises for missing path or
    malformed lines -- malformed lines are skipped.
    """
    if not path.exists():
        return [], offset_bytes
    events: list[Event] = []
    new_offset = offset_bytes
    with path.open("rb") as f:
        f.seek(offset_bytes)
        remainder = b""
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            remainder += chunk
            *complete_lines, remainder = remainder.split(b"\n")
            for raw_line in complete_lines:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    new_offset += len(raw_line) + 1
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    new_offset += len(raw_line) + 1
                    continue
                ev = _parse_event_dict(d)
                if ev is not None:
                    events.append(ev)
                new_offset += len(raw_line) + 1
        # remainder stays in the file for the next call; do not advance
    return events, new_offset


def update_state_with_event(state: StreamState, event: Event) -> None:
    """Incremental thread-state update.

    Mirrors build_threads() semantics so a streaming run converges to the
    same thread map a retrospective full-scan would produce on the same
    event log (verified by test_incremental_update_matches_full_rescan).
    """
    if not event.task_id:
        state.events.append(event)
        return
    th = state.threads.get(event.task_id)
    if th is None:
        th = TaskThread(
            task_id=event.task_id,
            first_agent=event.agent,
            first_ts=event.ts,
        )
        state.threads[event.task_id] = th
    th.participants.add(event.agent)
    th.events.append(event)
    if event.type == "claim" and th.claim_agent is None:
        th.claim_agent = event.agent
        th.claim_ts = event.ts
    state.events.append(event)


def get_current_metrics(
    state: StreamState,
    *,
    statuses: dict[str, str] | None = None,
    profile_config_ref: str = "default",
) -> dict:
    """Compute the current metrics block + snapshot envelope.

    Equivalent to build_report() but operating on the live in-memory
    StreamState rather than a one-shot file read.
    """
    statuses = statuses if statuses is not None else _load_statuses()
    metrics = compute_aligned_metrics(state.threads, statuses)
    last_event_ts = state.events[-1].ts.isoformat() if state.events else ""
    return {
        "schema_version": STREAM_SCHEMA_VERSION,
        "schema_aligned_with": SCHEMA_ALIGNED_WITH,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "live",
        "profile_config_ref": profile_config_ref,
        "source": {
            "events_path": str(EVENTS_PATH),
            "event_count": len(state.events),
            "last_event_ts_utc": last_event_ts,
            "cursor_bytes": state.cursor_bytes,
        },
        "metrics": metrics,
        "formal_statuses": statuses,
        "snapshot_count": state.snapshot_count,
    }


def stream(
    *,
    events_path: Path | str = EVENTS_PATH,
    emit_interval_s: float = 30.0,
    profile_config_ref: str = "default",
    emit_snapshot: Callable[[dict], str],
    initial_state: StreamState | None = None,
    statuses: dict[str, str] | None = None,
    clock_fn: Callable[[], float] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    stop_after_snapshots: int | None = None,
    poll_interval_s: float = 1.0,
) -> StreamState:
    """Tail events.jsonl + emit metric snapshots on an interval.

    Pluggable hooks for deterministic testing:
    * clock_fn -- monotonic time source (default time.monotonic).
    * sleep_fn -- sleep between polls (default time.sleep).
    * stop_after_snapshots -- bound the loop for tests; None = run
      until KeyboardInterrupt.

    Snapshot envelope is computed by get_current_metrics() and passed
    to emit_snapshot(). Caller's emit_snapshot is responsible for
    persisting the snapshot to MAGMA + StateHandle (see acceptance
    criteria 2 and 3 of the spec).

    Returns the final StreamState (useful for tests).
    """
    import time as _time

    state = initial_state if initial_state is not None else StreamState()
    path = Path(events_path)
    statuses = statuses if statuses is not None else _load_statuses()
    clock = clock_fn or _time.monotonic
    sleep = sleep_fn or _time.sleep

    last_emit = clock()
    while True:
        new_events, new_offset = read_events_from_offset(
            path, state.cursor_bytes
        )
        state.cursor_bytes = new_offset
        for ev in new_events:
            update_state_with_event(state, ev)

        now = clock()
        if now - last_emit >= emit_interval_s:
            snapshot = get_current_metrics(
                state,
                statuses=statuses,
                profile_config_ref=profile_config_ref,
            )
            state.snapshot_count += 1
            state.last_snapshot_ts_utc = snapshot["generated_at_utc"]
            emit_snapshot(snapshot)
            last_emit = now
            if (stop_after_snapshots is not None
                    and state.snapshot_count >= stop_after_snapshots):
                return state

        try:
            sleep(poll_interval_s)
        except KeyboardInterrupt:
            return state


# --- end streaming mode --------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default=str(EVENTS_PATH))
    ap.add_argument("--since-hours", type=float, default=48.0)
    ap.add_argument("--approach", choices=["B"], default="B")
    ap.add_argument("--out", default="-")
    args = ap.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
    events = parse_events(Path(args.events), cutoff)
    threads = build_threads(events)

    report = build_report(
        events,
        threads,
        events_path=args.events,
        since_hours=args.since_hours,
        cutoff=cutoff,
        approach=args.approach,
    )
    body = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if args.out == "-":
        print(body)
    else:
        Path(args.out).write_text(body, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
