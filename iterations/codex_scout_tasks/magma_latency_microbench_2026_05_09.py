# SPDX-License-Identifier: Apache-2.0
"""Repeatable MAGMA latency scout microbenchmark.

This is a scout artifact, not production code. It uses a fixed snapshot
manifest so before/after PRs can rerun the same synthetic workload without
environment drift.

Default:
    python iterations/codex_scout_tasks/magma_latency_microbench_2026_05_09.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.magma.audit_projector import AuditEntry, AuditProjector
from waggledance.core.magma.event_log_adapter import EventLogAdapter
from waggledance.core.magma.replay_engine import ReplayAdapter
from waggledance.core.magma.trust_adapter import TrustAdapter
from waggledance.core.magma.vector_events import (
    read_events,
    solver_upserted,
    vector_upsert_requested,
    emit_many,
)


DEFAULT_SNAPSHOT = Path(__file__).with_name(
    "magma_latency_snapshot_2026_05_09.json"
)


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256_12(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()[:12]


def _now_ms() -> float:
    return time.perf_counter_ns() / 1_000_000.0


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _timed(name: str, fn: Callable[[], Any]) -> tuple[str, float, Any]:
    started = _now_ms()
    result = fn()
    return name, _now_ms() - started, result


def _summary(name: str, samples_ms: list[float], count: int | None = None) -> dict:
    total = sum(samples_ms)
    return {
        "operation": name,
        "samples": len(samples_ms),
        "count": count if count is not None else len(samples_ms),
        "total_ms": round(total, 4),
        "p50_ms": round(_percentile(samples_ms, 50), 4),
        "p95_ms": round(_percentile(samples_ms, 95), 4),
        "p99_ms": round(_percentile(samples_ms, 99), 4),
        "max_ms": round(max(samples_ms) if samples_ms else 0.0, 4),
    }


def _event_type(i: int) -> str:
    return (
        "goal.accepted",
        "action.executed",
        "policy.approved",
        "capability.selected",
        "learning.case_graded",
    )[i % 5]


class _NoopLegacyAudit:
    def record(self, **_: Any) -> None:
        return None


class _NoopLegacyLedger:
    def log(self, **_: Any) -> None:
        return None

    def log_case_trajectory(self, *args: Any, **kwargs: Any) -> None:
        return None

    def log_specialist_training(self, *args: Any, **kwargs: Any) -> None:
        return None


def bench_event_log(manifest: dict) -> list[dict]:
    n = int(manifest["counts"]["event_log_events"])
    cells = manifest["cells"]
    adapter = EventLogAdapter(legacy_ledger=_NoopLegacyLedger())

    name, elapsed, _ = _timed(
        "EventLogAdapter.log_event bulk",
        lambda: [
            adapter.log_event(
                _event_type(i),
                source=f"cell:{cells[i % len(cells)]}",
                goal_id=f"goal-{i % 256:04d}",
                capability_id=f"solver-{i % 1024:04d}",
                latency_ms=float(i % 37),
            )
            for i in range(n)
        ],
    )
    rows = [_summary(name, [elapsed], count=n)]

    query_samples = []
    for event in ("goal.accepted", "action.executed", "learning.case_graded"):
        _, ms, found = _timed(
            f"EventLogAdapter.query {event}",
            lambda event=event: adapter.query(event_type=event, limit=100),
        )
        query_samples.append(ms)
        if not found:
            raise AssertionError(f"query returned no rows for {event}")
    rows.append(_summary("EventLogAdapter.query by type", query_samples))

    _, ms, counts = _timed("EventLogAdapter.count_by_type", adapter.count_by_type)
    if not counts:
        raise AssertionError("count_by_type returned empty counts")
    rows.append(_summary("EventLogAdapter.count_by_type", [ms]))

    _, ms, stats = _timed("EventLogAdapter.stats", adapter.stats)
    if stats["total_entries"] == 0:
        raise AssertionError("stats returned zero entries")
    rows.append(_summary("EventLogAdapter.stats", [ms]))
    return rows


def bench_audit_projector(manifest: dict) -> list[dict]:
    n = int(manifest["counts"]["audit_entries"])
    cells = manifest["cells"]
    projector = AuditProjector(audit_log=_NoopLegacyAudit())

    def record_all() -> None:
        for i in range(n):
            projector.record(AuditEntry(
                event_type=_event_type(i),
                source=f"cell:{cells[i % len(cells)]}",
                payload={"i": i, "cell": cells[i % len(cells)]},
                goal_id=f"goal-{i % 128:04d}",
                capability_id=f"solver-{i % 1024:04d}",
            ))

    name, elapsed, _ = _timed("AuditProjector.record bulk", record_all)
    rows = [_summary(name, [elapsed], count=n)]

    samples = []
    for i in range(128):
        _, ms, _ = _timed(
            "AuditProjector.query_by_goal",
            lambda i=i: projector.query_by_goal(f"goal-{i:04d}"),
        )
        samples.append(ms)
    rows.append(_summary("AuditProjector.query_by_goal", samples))

    samples = []
    for event in ("goal.accepted", "action.executed", "learning.case_graded"):
        _, ms, _ = _timed(
            "AuditProjector.query_by_event_type",
            lambda event=event: projector.query_by_event_type(event),
        )
        samples.append(ms)
    rows.append(_summary("AuditProjector.query_by_event_type", samples))
    return rows


def bench_trust_adapter(manifest: dict) -> list[dict]:
    targets = int(manifest["counts"]["trust_targets"])
    per_target = int(manifest["counts"]["trust_observations_per_target"])
    cells = manifest["cells"]
    adapter = TrustAdapter()

    def record_all() -> None:
        for t in range(targets):
            for j in range(per_target):
                adapter.record_observation(
                    "solver",
                    f"solver-{t:05d}",
                    success=((t + j) % 7) != 0,
                    confidence=0.5 + ((t + j) % 50) / 100.0,
                    latency_ms=float((t * 3 + j) % 41),
                    quality_path=cells[t % len(cells)],
                    context="simulated" if j % 11 == 0 else "actual",
                )

    name, elapsed, _ = _timed("TrustAdapter.record_observation bulk", record_all)
    rows = [_summary(name, [elapsed], count=targets * per_target)]

    samples = []
    for t in range(targets):
        _, ms, score = _timed(
            "TrustAdapter.get_trust_score",
            lambda t=t: adapter.get_trust_score("solver", f"solver-{t:05d}"),
        )
        if not (0.0 <= score <= 1.0):
            raise AssertionError("trust score out of range")
        samples.append(ms)
    rows.append(_summary("TrustAdapter.get_trust_score", samples))

    _, ms, ranking = _timed(
        "TrustAdapter.get_ranking all solver targets",
        lambda: adapter.get_ranking("solver", limit=20),
    )
    if len(ranking) != 20:
        raise AssertionError("trust ranking returned wrong length")
    rows.append(_summary("TrustAdapter.get_ranking", [ms], count=targets))
    return rows


def bench_replay_adapter(manifest: dict) -> list[dict]:
    missions = int(manifest["counts"]["replay_missions"])
    per_mission = int(manifest["counts"]["replay_events_per_mission"])
    cells = manifest["cells"]
    adapter = ReplayAdapter()

    def record_all() -> None:
        for m in range(missions):
            goal = f"mission-{m:04d}"
            for step in range(per_mission):
                adapter.record_mission_event(
                    goal,
                    event_type=("start", "route", "execute", "verify")[step % 4],
                    payload={"cell": cells[(m + step) % len(cells)]},
                    step_order=step,
                    capability_id=f"solver-{(m * per_mission + step) % 4096:05d}",
                )

    name, elapsed, _ = _timed("ReplayAdapter.record_mission_event bulk", record_all)
    rows = [_summary(name, [elapsed], count=missions * per_mission)]

    samples = []
    for m in range(missions):
        _, ms, replay = _timed(
            "ReplayAdapter.get_mission_replay",
            lambda m=m: adapter.get_mission_replay(f"mission-{m:04d}"),
        )
        if replay is None:
            raise AssertionError("mission replay missing")
        samples.append(ms)
    rows.append(_summary("ReplayAdapter.get_mission_replay", samples))

    _, ms, listed = _timed("ReplayAdapter.list_missions", adapter.list_missions)
    if not listed:
        raise AssertionError("list_missions returned no missions")
    rows.append(_summary("ReplayAdapter.list_missions", [ms], count=missions))
    return rows


def bench_vector_events(manifest: dict) -> list[dict]:
    n = int(manifest["counts"]["vector_events"])
    cells = manifest["cells"]
    families = manifest["families"]
    events = []
    for i in range(n):
        cell = cells[i % len(cells)]
        model = f"solver-{i:05d}"
        signature = hashlib.sha256(f"{model}:{families[i % len(families)]}".encode()).hexdigest()[:16]
        if i % 2 == 0:
            events.append(solver_upserted(cell, model, signature, f"synthetic/{model}.yaml"))
        else:
            events.append(vector_upsert_requested(cell, model, signature, reason="microbench"))

    with tempfile.TemporaryDirectory(prefix="magma-vector-events-") as tmp:
        path = Path(tmp) / "events.jsonl"
        name, write_ms, _ = _timed(
            "vector_events.emit_many JSONL",
            lambda: emit_many(events, path),
        )
        rows = [_summary(name, [write_ms], count=n)]

        name, read_ms, read_back = _timed(
            "vector_events.read_events full scan",
            lambda: list(read_events(path)),
        )
        if len(read_back) != n:
            raise AssertionError(f"read back {len(read_back)} events, expected {n}")
        rows.append(_summary(name, [read_ms], count=n))
    return rows


def run(snapshot_path: Path) -> dict:
    manifest = json.loads(snapshot_path.read_text(encoding="utf-8"))
    rows = []
    for bench in (
        bench_event_log,
        bench_audit_projector,
        bench_trust_adapter,
        bench_replay_adapter,
        bench_vector_events,
    ):
        rows.extend(bench(manifest))
    return {
        "schema_version": 1,
        "benchmark_id": "r17-magma-latency-microbench-2026-05-09",
        "snapshot_path": str(snapshot_path),
        "snapshot_sha256_12": _sha256_12(manifest),
        "counts": manifest["counts"],
        "operations": rows,
        "threshold_10ms": [
            row for row in rows
            if row["total_ms"] > 10.0 or row["p99_ms"] > 10.0
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument(
        "--out-json",
        type=Path,
        default=Path(".codex-audit") / "r17_magma_latency_microbench.json",
    )
    args = parser.parse_args()

    result = run(args.snapshot)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(
        json.dumps(result, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
