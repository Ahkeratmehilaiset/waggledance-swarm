# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/check_gate_signal_taskid_convention.py.

Forge vectors (per claude-rco-1): a gate-decision signal on a non-headRefName
task_id WARNS; on a known headRefName it is silent; coordination traffic on a
reanchor/coordination task is NOT warned; a recently-merged headRefName is
handled (silent when included in the known set). Read-only/WARN-only: the tool
never blocks. Offline/deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.check_gate_signal_taskid_convention import (  # noqa: E402
    find_gate_signal_taskid_warnings,
    _read_events,
    main,
)

CANONICAL = "codex-lead-1/runtime-receipt-verifier-metrics-20260620"
REANCHOR = "codex-lead-1/pr1330-reanchor-post1306-20260620"


def _event(agent, type_, status, task_id):
    return {
        "ts_utc": "2026-06-20T18:00:00Z",
        "agent": agent,
        "type": type_,
        "status": status,
        "task_id": task_id,
        "message": "",
    }


def test_gate_signal_on_non_headref_task_warns() -> None:
    # The exact #1330 mis-post: rco_pass posted on the reanchor coordination
    # task, not the canonical headRefName.
    events = [_event("claude-rco-1", "decision", "rco_pass", REANCHOR)]
    warnings = find_gate_signal_taskid_warnings(events, [CANONICAL])
    assert len(warnings) == 1
    assert warnings[0]["task_id"] == REANCHOR
    assert warnings[0]["status"] == "rco_pass"
    assert warnings[0]["reason"] == "gate_decision_signal_taskid_not_pr_headref"


def test_gate_signal_on_known_headref_silent() -> None:
    events = [
        _event("claude-rco-1", "decision", "rco_pass", CANONICAL),
        _event("codex-tools-1", "decision", "build_consensus_pass", CANONICAL),
    ]
    assert find_gate_signal_taskid_warnings(events, [CANONICAL]) == []


def test_changes_requested_on_non_headref_task_warns() -> None:
    # changes_requested is a gate-decision status too -> must be scoped.
    events = [_event("claude-rco-1", "decision", "changes_requested", REANCHOR)]
    warnings = find_gate_signal_taskid_warnings(events, [CANONICAL])
    assert len(warnings) == 1
    assert warnings[0]["status"] == "changes_requested"


def test_coordination_traffic_on_reanchor_task_not_warned() -> None:
    # Coordination statuses legitimately use coordination tasks -> never warned.
    events = [
        _event("codex-lead-1", "handoff", "pushed_ci_running", REANCHOR),
        _event("codex-lead-1", "wake_request", "review_requested", REANCHOR),
        _event("codex-lead-1", "message", "info", REANCHOR),
        _event("codex-lead-1", "claim", "active", REANCHOR),
        _event("codex-lead-1", "status", "queue_status", REANCHOR),
    ]
    assert find_gate_signal_taskid_warnings(events, [CANONICAL]) == []


def test_recently_merged_headref_handled_when_in_known_set() -> None:
    # A gate-decision signal on a just-merged PR's headRefName is legitimate;
    # including merged headRefNames in the known set keeps it silent.
    merged_headref = "fable-5/changes-requested-cleared-clear-status-20260620"
    events = [_event("claude-rco-1", "decision", "rco_pass", merged_headref)]
    # Not in known set -> warns (true coordination-task style mis-post).
    assert len(find_gate_signal_taskid_warnings(events, [CANONICAL])) == 1
    # Included (open + recently-merged) -> silent.
    assert find_gate_signal_taskid_warnings(events, [CANONICAL, merged_headref]) == []


def test_only_gate_decision_statuses_scoped() -> None:
    # A non-gate-decision decision status (e.g. acknowledged) is not scoped.
    events = [_event("fable-5", "decision", "acknowledged", REANCHOR)]
    assert find_gate_signal_taskid_warnings(events, [CANONICAL]) == []


def test_read_events_skips_bare_null_and_malformed(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                "null",
                "{not json",
                "[1, 2, 3]",
                json.dumps(_event("claude-rco-1", "decision", "rco_pass", REANCHOR)),
            ]
        ),
        encoding="utf-8",
    )
    parsed = _read_events(events_path)
    assert parsed == [_event("claude-rco-1", "decision", "rco_pass", REANCHOR)]


def test_main_is_warn_only_exit_zero(tmp_path: Path, capsys) -> None:
    # Even with a warning present, the CLI exits 0 (WARN-only, never blocks).
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        json.dumps(_event("claude-rco-1", "decision", "rco_pass", REANCHOR)) + "\n",
        encoding="utf-8",
    )
    rc = main(["--events", str(events_path), "--headref", CANONICAL, "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["warning_count"] == 1
    assert out["warnings"][0]["task_id"] == REANCHOR


def test_main_defaults_to_runtime_bridge_root(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    runtime_bridge = tmp_path / "runtime-bridge"
    shared = runtime_bridge / "shared"
    shared.mkdir(parents=True)
    (shared / "events.jsonl").write_text(
        json.dumps(_event("claude-rco-1", "decision", "rco_pass", REANCHOR)) + "\n",
        encoding="utf-8",
    )
    shadow_repo = tmp_path / "shadow-repo"
    shadow_repo.mkdir()
    monkeypatch.chdir(shadow_repo)
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_bridge))

    rc = main(["--headref", CANONICAL, "--json"])

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["warning_count"] == 1
    assert out["warnings"][0]["task_id"] == REANCHOR
