# SPDX-License-Identifier: BUSL-1.1
"""Verify sim_orchestrator emits the same metric vocabulary as the
prospective Flight Plan builder.

Per ``codex-orchestrator-sim-b-opinion-2026-05-12 / consensus_accepted``
the two tools share one schema: ``metrics`` field names must match the
Flight Plan schema exactly, and given the same events.jsonl input the
two ``metrics`` blocks must produce identical numbers.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "sim_orchestrator.py"


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )


def _sample_events() -> list[dict]:
    return [
        {
            "ts_utc": "2026-05-12T00:00:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "finding",
            "status": "open",
            "task_id": "thread-answered",
            "message": "Please review PR #1",
        },
        {
            "ts_utc": "2026-05-12T00:01:00Z",
            "agent": "codex",
            "to": "claude",
            "type": "message",
            "status": "answered",
            "task_id": "thread-answered",
            "message": "Reviewed.",
        },
        {
            "ts_utc": "2026-05-12T00:02:00Z",
            "agent": "codex",
            "to": "claude",
            "type": "claim",
            "status": "active",
            "task_id": "thread-claimed",
            "message": "Claiming write scope.",
        },
        {
            "ts_utc": "2026-05-12T00:03:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "status": "open",
            "task_id": "thread-unresolved",
            "message": "Need Codex action.",
        },
        {
            "ts_utc": "2026-05-12T00:04:00Z",
            "agent": "codex",
            "to": "claude",
            "type": "status",
            "status": "",
            "task_id": "thread-unresolved",
            "message": "Codex saw this.",
        },
        {
            "ts_utc": "2026-05-12T00:05:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "decision",
            "status": "consensus_accepted",
            "task_id": "thread-consensus",
            "message": "Consensus accepted.",
        },
        {
            "ts_utc": "2026-05-12T00:06:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "decision",
            "status": "rco_requested",
            "task_id": "thread-rco",
            "message": "Please RCO this PR.",
        },
    ]


def _seed_bridge(tmp_path: Path, events: list[dict]) -> tuple[Path, Path]:
    bridge_root = tmp_path / ".agent-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    _write_jsonl(events_path, events)
    return bridge_root, events_path


SCHEMA_METRIC_FIELDS = {
    "task_threads_total",
    "threads_with_claim",
    "claim_coverage_pct",
    "multi_agent_threads",
    "multi_agent_threads_without_claim",
    "formal_rco_threads",
    "consensus_topics",
}

SCHEMA_FORMAL_STATUSES = {
    "rco_requested",
    "rco_done",
    "consensus_proposal",
    "consensus_accepted",
    "claim_required",
    "missing_claim",
}


def test_cli_defaults_to_runtime_bridge_root_env(tmp_path: Path) -> None:
    bridge_root, events_path = _seed_bridge(tmp_path / "runtime", _sample_events())
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(bridge_root)
    env.pop("AGENT_BRIDGE_ROOT", None)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--since-hours",
            "100000",
            "--out",
            "-",
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["source"]["events_path"] == str(events_path)
    assert payload["source"]["event_count"] == len(_sample_events())


def test_cli_explicit_events_overrides_runtime_bridge_root_env(tmp_path: Path) -> None:
    bridge_root, _runtime_events = _seed_bridge(tmp_path / "runtime", _sample_events())
    explicit_events = tmp_path / "explicit.jsonl"
    _write_jsonl(explicit_events, [_sample_events()[0]])
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(bridge_root)
    env.pop("AGENT_BRIDGE_ROOT", None)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--events",
            str(explicit_events),
            "--since-hours",
            "100000",
            "--out",
            "-",
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["source"]["events_path"] == str(explicit_events)
    assert payload["source"]["event_count"] == 1


def test_sim_orchestrator_emits_flight_plan_metric_vocabulary(tmp_path: Path) -> None:
    sim = importlib.import_module("tools.sim_orchestrator")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(events_path, _sample_events())

    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = sim.parse_events(events_path, cutoff)
    threads = sim.build_threads(events)
    report = sim.build_report(
        events,
        threads,
        events_path=str(events_path),
        since_hours=720.0,
        cutoff=cutoff,
        approach="B",
        now_utc="2026-05-12T01:00:00Z",
    )

    assert report["schema_version"] == "agent-flight-plan-retrospective-v1"
    assert report["schema_aligned_with"] == "agent-flight-plan-v1"
    assert set(report["metrics"].keys()) == SCHEMA_METRIC_FIELDS
    assert set(report["formal_statuses"].keys()) == SCHEMA_FORMAL_STATUSES


def test_sim_orchestrator_metrics_match_flight_plan_builder(tmp_path: Path) -> None:
    """The same events.jsonl must produce identical ``metrics`` numbers
    in both the retrospective (sim_orchestrator) and prospective
    (build_agent_flight_plan) outputs."""
    sim = importlib.import_module("tools.sim_orchestrator")
    afp = importlib.import_module("tools.build_agent_flight_plan")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(events_path, _sample_events())

    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = sim.parse_events(events_path, cutoff)
    threads = sim.build_threads(events)
    sim_report = sim.build_report(
        events,
        threads,
        events_path=str(events_path),
        since_hours=720.0,
        cutoff=cutoff,
        approach="B",
        now_utc="2026-05-12T01:00:00Z",
    )

    afp_plan = afp.build_plan(
        events_path=events_path,
        objective="alignment test",
        target_agent="codex",
        now_utc="2026-05-12T01:00:00Z",
    )

    assert sim_report["metrics"] == afp_plan["metrics"]
    assert sim_report["formal_statuses"] == afp_plan["formal_statuses"]


def test_sim_orchestrator_keeps_retrospective_extensions(tmp_path: Path) -> None:
    sim = importlib.import_module("tools.sim_orchestrator")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(events_path, _sample_events())

    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = sim.parse_events(events_path, cutoff)
    threads = sim.build_threads(events)
    report = sim.build_report(
        events,
        threads,
        events_path=str(events_path),
        since_hours=720.0,
        cutoff=cutoff,
        approach="B",
    )

    ext = report["retrospective_extensions"]
    assert "handshake_examples" in ext
    assert "independent_convergences" in ext
    assert "pr_lifecycle" in ext
    assert "finding_quality" in ext
    assert "lane_balance" in ext
    assert "approach_b_projection" in ext
    assert ext["approach_b_projection"]["multi_agent_threads_without_claim"] == \
        report["metrics"]["multi_agent_threads_without_claim"]
