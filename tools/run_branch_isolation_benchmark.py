# SPDX-License-Identifier: BUSL-1.1
"""R22 2D branch-isolation baseline benchmark.

This is a measurement-only harness for the current 2D hex topology and
global ControlPlaneDB persistence model. It intentionally does not add
3D coordinates, sharding, schema migrations, or feature flags.

The benchmark measures whether runtime-gap writes for one 2D branch
affect write latency for another branch through the shared ControlPlaneDB
connection/lock/WAL path.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.application.services.hex_topology_registry import (  # noqa: E402
    HexTopologyRegistry,
)
from waggledance.core.storage.control_plane import ControlPlaneDB  # noqa: E402


DEFAULT_CONFIG = ROOT / "configs" / "hex_cells.yaml"


@dataclass(frozen=True)
class BenchmarkConfig:
    db_path: Path
    config_path: Path = DEFAULT_CONFIG
    out_json: Path | None = None
    repeats: int = 3
    probe_events: int = 200
    hot_events: int = 1000
    uniform_events_per_branch: int = 80
    cold_flood_events_per_branch: int = 120
    probe_branch: str = "hub"
    hot_branch: str = "bee_ops"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_head() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:  # noqa: BLE001 - benchmark metadata must not fail the run.
        return "unknown"


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _summarize_ms(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {
            "count": 0,
            "min_ms": 0.0,
            "mean_ms": 0.0,
            "median_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "max_ms": 0.0,
        }
    return {
        "count": len(values),
        "min_ms": min(values),
        "mean_ms": statistics.fmean(values),
        "median_ms": statistics.median(values),
        "p95_ms": _percentile(values, 0.95),
        "p99_ms": _percentile(values, 0.99),
        "max_ms": max(values),
    }


def _cv(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = statistics.fmean(values)
    if mean == 0:
        return 0.0
    return statistics.pstdev(values) / mean


def _load_branch_ids(config_path: Path) -> list[str]:
    registry = HexTopologyRegistry(config_path=str(config_path), agents=[])
    branch_ids = sorted(registry.cells)
    if len(branch_ids) < 2:
        raise RuntimeError(
            f"branch-isolation benchmark needs at least 2 hex cells; got {branch_ids}"
        )
    return branch_ids


def _record_signal(db: ControlPlaneDB, *, branch: str, idx: int, profile: str) -> None:
    db.record_runtime_gap_signal(
        "branch_isolation_probe",
        family_kind="r22_branch_isolation",
        cell_coord=branch,
        signal_payload=json.dumps(
            {"profile": profile, "branch": branch, "idx": idx},
            sort_keys=True,
            separators=(",", ":"),
        ),
        weight=1.0,
    )


def _timed_record(
    db: ControlPlaneDB,
    *,
    branch: str,
    idx: int,
    profile: str,
) -> float:
    started = time.perf_counter_ns()
    _record_signal(db, branch=branch, idx=idx, profile=profile)
    ended = time.perf_counter_ns()
    return (ended - started) / 1_000_000.0


def _probe_series(
    db: ControlPlaneDB,
    *,
    branch: str,
    count: int,
    profile: str,
) -> list[float]:
    return [
        _timed_record(db, branch=branch, idx=idx, profile=profile)
        for idx in range(count)
    ]


def _run_hot_writer(
    db: ControlPlaneDB,
    *,
    branch: str,
    max_events: int,
    stop_event: threading.Event,
    ready_event: threading.Event,
    profile: str,
) -> None:
    ready_event.set()
    idx = 0
    while idx < max_events and not stop_event.is_set():
        _record_signal(db, branch=branch, idx=idx, profile=profile)
        idx += 1


def _single_hot_interference(
    db: ControlPlaneDB,
    *,
    probe_branch: str,
    hot_branch: str,
    probe_events: int,
    hot_events: int,
    repeat: int,
) -> dict[str, Any]:
    stop_event = threading.Event()
    ready_event = threading.Event()
    worker = threading.Thread(
        target=_run_hot_writer,
        kwargs={
            "db": db,
            "branch": hot_branch,
            "max_events": hot_events,
            "stop_event": stop_event,
            "ready_event": ready_event,
            "profile": f"single_hot_interference_r{repeat}",
        },
        daemon=True,
    )
    worker.start()
    ready_event.wait(timeout=5.0)
    latencies = _probe_series(
        db,
        branch=probe_branch,
        count=probe_events,
        profile=f"single_hot_probe_r{repeat}",
    )
    stop_event.set()
    worker.join(timeout=5.0)
    return {
        "repeat": repeat,
        "probe_branch": probe_branch,
        "hot_branch": hot_branch,
        "branch_touch_count_avg": 1.0,
        "latency": _summarize_ms(latencies),
    }


def _uniform_multi_branch(
    db: ControlPlaneDB,
    *,
    branches: list[str],
    events_per_branch: int,
    repeat: int,
) -> dict[str, Any]:
    by_branch: dict[str, dict[str, Any]] = {}
    for branch in branches:
        latencies = _probe_series(
            db,
            branch=branch,
            count=events_per_branch,
            profile=f"uniform_multi_branch_r{repeat}",
        )
        by_branch[branch] = {
            "branch_touch_count_avg": 1.0,
            "latency": _summarize_ms(latencies),
        }
    p99s = [
        float(item["latency"]["p99_ms"])
        for item in by_branch.values()
        if int(item["latency"]["count"]) > 0
    ]
    return {
        "repeat": repeat,
        "branches": by_branch,
        "p99_cv": _cv(p99s),
        "p99_max_min_ratio": (max(p99s) / min(p99s)) if p99s and min(p99s) > 0 else 0.0,
    }


def _adversarial_cold_flood(
    db: ControlPlaneDB,
    *,
    branches: list[str],
    probe_branch: str,
    events_per_branch: int,
    probe_events: int,
    repeat: int,
) -> dict[str, Any]:
    cold_branches = [b for b in branches if b != probe_branch]
    stop_event = threading.Event()
    ready_events: list[threading.Event] = []
    workers: list[threading.Thread] = []
    for branch in cold_branches:
        ready_event = threading.Event()
        ready_events.append(ready_event)
        worker = threading.Thread(
            target=_run_hot_writer,
            kwargs={
                "db": db,
                "branch": branch,
                "max_events": events_per_branch,
                "stop_event": stop_event,
                "ready_event": ready_event,
                "profile": f"adversarial_cold_flood_r{repeat}",
            },
            daemon=True,
        )
        workers.append(worker)
        worker.start()
    for ready_event in ready_events:
        ready_event.wait(timeout=5.0)
    latencies = _probe_series(
        db,
        branch=probe_branch,
        count=probe_events,
        profile=f"adversarial_probe_r{repeat}",
    )
    stop_event.set()
    for worker in workers:
        worker.join(timeout=5.0)
    return {
        "repeat": repeat,
        "probe_branch": probe_branch,
        "cold_branches": cold_branches,
        "branch_touch_count_avg": 1.0,
        "latency": _summarize_ms(latencies),
    }


def run_benchmark(config: BenchmarkConfig) -> dict[str, Any]:
    branch_ids = _load_branch_ids(config.config_path)
    if config.probe_branch not in branch_ids:
        raise RuntimeError(f"probe branch {config.probe_branch!r} not in {branch_ids}")
    if config.hot_branch not in branch_ids:
        raise RuntimeError(f"hot branch {config.hot_branch!r} not in {branch_ids}")
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    if config.db_path.exists():
        config.db_path.unlink()
    db = ControlPlaneDB(config.db_path)
    try:
        db.migrate()
        idle_runs = []
        hot_runs = []
        uniform_runs = []
        cold_runs = []
        for repeat in range(1, config.repeats + 1):
            idle_latencies = _probe_series(
                db,
                branch=config.probe_branch,
                count=config.probe_events,
                profile=f"idle_probe_r{repeat}",
            )
            idle_runs.append(
                {
                    "repeat": repeat,
                    "probe_branch": config.probe_branch,
                    "branch_touch_count_avg": 1.0,
                    "latency": _summarize_ms(idle_latencies),
                }
            )
            hot_runs.append(
                _single_hot_interference(
                    db,
                    probe_branch=config.probe_branch,
                    hot_branch=config.hot_branch,
                    probe_events=config.probe_events,
                    hot_events=config.hot_events,
                    repeat=repeat,
                )
            )
            uniform_runs.append(
                _uniform_multi_branch(
                    db,
                    branches=branch_ids,
                    events_per_branch=config.uniform_events_per_branch,
                    repeat=repeat,
                )
            )
            cold_runs.append(
                _adversarial_cold_flood(
                    db,
                    branches=branch_ids,
                    probe_branch=config.probe_branch,
                    events_per_branch=config.cold_flood_events_per_branch,
                    probe_events=config.probe_events,
                    repeat=repeat,
                )
            )
        idle_p99 = statistics.fmean(
            float(run["latency"]["p99_ms"]) for run in idle_runs
        )
        hot_p99 = statistics.fmean(
            float(run["latency"]["p99_ms"]) for run in hot_runs
        )
        cold_p99 = statistics.fmean(
            float(run["latency"]["p99_ms"]) for run in cold_runs
        )
        result = {
            "proof_version": 1,
            "generated_at_utc": _utc_iso(),
            "git_head": _git_head(),
            "purpose": "2d_branch_isolation_baseline",
            "topology": {
                "model": "2d_axial_hex",
                "config_path": str(config.config_path),
                "branch_ids": branch_ids,
                "probe_branch": config.probe_branch,
                "hot_branch": config.hot_branch,
            },
            "database": {
                "mode": "single_global_control_plane_db",
                "db_path": str(config.db_path),
                "schema_version": db.schema_version(),
            },
            "parameters": {
                "repeats": config.repeats,
                "probe_events": config.probe_events,
                "hot_events": config.hot_events,
                "uniform_events_per_branch": config.uniform_events_per_branch,
                "cold_flood_events_per_branch": config.cold_flood_events_per_branch,
            },
            "machine": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "processor": platform.processor(),
            },
            "profiles": {
                "idle_probe": idle_runs,
                "single_hot_interference": hot_runs,
                "uniform_multi_branch": uniform_runs,
                "adversarial_cold_flood": cold_runs,
            },
            "summary": {
                "idle_probe_p99_ms_mean": idle_p99,
                "single_hot_probe_p99_ms_mean": hot_p99,
                "adversarial_probe_p99_ms_mean": cold_p99,
                "single_hot_degradation_ratio": hot_p99 / idle_p99 if idle_p99 > 0 else 0.0,
                "adversarial_degradation_ratio": cold_p99 / idle_p99 if idle_p99 > 0 else 0.0,
                "uniform_p99_cv_mean": statistics.fmean(
                    float(run["p99_cv"]) for run in uniform_runs
                ),
                "branch_touch_count_hit_case_target": 1.0,
            },
        }
    finally:
        db.close()
    if config.out_json is not None:
        config.out_json.parent.mkdir(parents=True, exist_ok=True)
        config.out_json.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Temporary SQLite DB path")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--probe-events", type=int, default=200)
    parser.add_argument("--hot-events", type=int, default=1000)
    parser.add_argument("--uniform-events-per-branch", type=int, default=80)
    parser.add_argument("--cold-flood-events-per-branch", type=int, default=120)
    parser.add_argument("--probe-branch", default="hub")
    parser.add_argument("--hot-branch", default="bee_ops")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_benchmark(
        BenchmarkConfig(
            db_path=args.db,
            config_path=args.config,
            out_json=args.out_json,
            repeats=args.repeats,
            probe_events=args.probe_events,
            hot_events=args.hot_events,
            uniform_events_per_branch=args.uniform_events_per_branch,
            cold_flood_events_per_branch=args.cold_flood_events_per_branch,
            probe_branch=args.probe_branch,
            hot_branch=args.hot_branch,
        )
    )
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
