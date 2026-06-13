# SPDX-License-Identifier: BUSL-1.1
"""Unit tests for tools/build_producer_burn_report.py (sprint S5)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.build_producer_burn_report import (
    CLAIM_GATES,
    build_burn_report,
    main,
)
from waggledance.core.magma.canonical import sha256_digest

NOW_TEXT = "2026-06-10T12:00:00Z"
NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
RESET_TEXT = "2026-06-11T12:00:00Z"  # 24h out
RESET = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


def _ev(agent: str, ts: str, type_: str = "decision") -> dict:
    return {"ts_utc": ts, "agent": agent, "type": type_, "task_id": "t", "status": "ok"}


def _busy(agent: str, n: int, minute0: int = 0) -> list:
    # n substantive events inside the last hour
    return [_ev(agent, f"2026-06-10T11:{minute0 + i:02d}:00Z") for i in range(n)]


def _heartbeats(agent: str, n: int) -> list:
    return [_ev(agent, f"2026-06-10T{6 + i:02d}:00:00Z", "heartbeat") for i in range(n)]


def _write(tmp_path: Path, events: list) -> Path:
    p = tmp_path / "events.jsonl"
    p.write_text(
        "\n".join(json.dumps(e) if isinstance(e, dict) else e for e in events) + "\n",
        encoding="utf-8",
    )
    return p


# --- status classification ----------------------------------------------------


def test_never_seen_when_no_events():
    art = build_burn_report(events=[], producers=("codex-lead-1",), now=NOW)
    assert art["per_producer"]["codex-lead-1"]["status"] == "never_seen"
    assert art["worst_status"] == "never_seen"
    assert art["escalated_producers"] == []


def test_idle_when_only_heartbeats():
    art = build_burn_report(
        events=_heartbeats("codex-tools-1", 10),
        producers=("codex-tools-1",),
        now=NOW,
    )
    entry = art["per_producer"]["codex-tools-1"]
    assert entry["status"] == "idle"
    assert "only_heartbeats_in_largest_window" in entry["status_reasons"]
    assert art["escalated_producers"] == []


def test_ok_when_within_budget():
    art = build_burn_report(
        events=_busy("codex-lead-1", 2),
        producers=("codex-lead-1",),
        now=NOW,
        warn_events_per_hour=6.0,
    )
    assert art["per_producer"]["codex-lead-1"]["status"] == "ok"
    assert art["worst_status"] == "ok"


def test_over_budget_on_rate_alone():
    art = build_burn_report(
        events=_busy("codex-lead-1", 30),  # 30/h in the 1h window
        producers=("codex-lead-1",),
        now=NOW,
        warn_events_per_hour=6.0,
    )
    entry = art["per_producer"]["codex-lead-1"]
    assert entry["status"] == "over_budget"
    assert any("burn_rate_over_warn" in r for r in entry["status_reasons"])
    assert art["escalated_producers"] == ["codex-lead-1"]


def test_approaching_cap_on_soft_budget():
    # 6 substantive events over the last 24h -> 0.25/h; projected to 24h reset = 6
    events = [_ev("codex-lead-1", f"2026-06-10T{h:02d}:00:00Z") for h in range(6, 12)]
    art = build_burn_report(
        events=events,
        producers=("codex-lead-1",),
        now=NOW,
        reset_at=RESET,
        projection_window_hours=24.0,
        warn_events_per_hour=6.0,
        soft_budget=5.0,
        hard_budget=10.0,
    )
    entry = art["per_producer"]["codex-lead-1"]
    assert entry["projected_substantive_events_to_reset"] == 6.0
    assert entry["status"] == "approaching_cap"
    assert art["escalated_producers"] == ["codex-lead-1"]


def test_over_budget_on_hard_projection():
    events = [_ev("codex-lead-1", f"2026-06-10T{h:02d}:00:00Z") for h in range(6, 12)]
    art = build_burn_report(
        events=events,
        producers=("codex-lead-1",),
        now=NOW,
        reset_at=RESET,
        projection_window_hours=24.0,
        warn_events_per_hour=100.0,  # keep rate out of it
        soft_budget=2.0,
        hard_budget=5.0,
    )
    entry = art["per_producer"]["codex-lead-1"]
    assert entry["status"] == "over_budget"
    assert any("projection_over_hard" in r for r in entry["status_reasons"])


def test_worst_status_is_max_severity_across_producers():
    events = _busy("codex-lead-1", 30) + _heartbeats("codex-tools-1", 5)
    art = build_burn_report(
        events=events,
        producers=("codex-lead-1", "codex-tools-1"),
        now=NOW,
        warn_events_per_hour=6.0,
    )
    assert art["per_producer"]["codex-lead-1"]["status"] == "over_budget"
    assert art["per_producer"]["codex-tools-1"]["status"] == "idle"
    assert art["worst_status"] == "over_budget"


# --- artifact contract --------------------------------------------------------


def test_digest_rederives_and_claim_gates_false():
    art = build_burn_report(events=_busy("codex-lead-1", 2), producers=("codex-lead-1",), now=NOW)
    core = {k: v for k, v in art.items() if k != "canonical_digest"}
    assert art["canonical_digest"] == sha256_digest(core)
    for gate in CLAIM_GATES:
        assert art[gate] is False
    assert art["advisory_only"] is True
    assert art["read_only"] is True


def test_deterministic():
    one = build_burn_report(events=_busy("codex-lead-1", 3), producers=("codex-lead-1",), now=NOW)
    two = build_burn_report(events=_busy("codex-lead-1", 3), producers=("codex-lead-1",), now=NOW)
    assert one == two


def test_malformed_lines_counted_not_fatal(tmp_path, capsys):
    path = _write(tmp_path, _busy("codex-lead-1", 2) + ["{bad json", json.dumps([1, 2])])
    rc = main(["--events", str(path), "--now", NOW_TEXT, "--producer", "codex-lead-1", "--json"])
    assert rc == 0
    art = json.loads(capsys.readouterr().out)
    assert art["malformed_lines"] == 2


# --- CLI ----------------------------------------------------------------------


def test_main_exit_4_on_escalation(tmp_path):
    path = _write(tmp_path, _busy("codex-lead-1", 30))
    rc = main(
        ["--events", str(path), "--now", NOW_TEXT, "--producer", "codex-lead-1",
         "--warn-events-per-hour", "6", "--json"]
    )
    assert rc == 4


def test_main_exit_0_when_all_ok(tmp_path):
    path = _write(tmp_path, _busy("codex-lead-1", 2))
    rc = main(["--events", str(path), "--now", NOW_TEXT, "--producer", "codex-lead-1"])
    assert rc == 0


def test_main_uses_runtime_bridge_root_env_by_default(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_bridge = tmp_path / "runtime" / ".agent-bridge"
    runtime_events = runtime_bridge / "shared" / "events.jsonl"
    runtime_events.parent.mkdir(parents=True)
    runtime_events.write_text(
        json.dumps(_ev("codex-lead-1", "2026-06-10T11:30:00Z")) + "\n",
        encoding="utf-8",
    )

    shadow_root = tmp_path / "shadow"
    shadow_events = shadow_root / ".agent-bridge" / "shared" / "events.jsonl"
    shadow_events.parent.mkdir(parents=True)
    shadow_events.write_text(
        json.dumps(_ev("codex-tools-1", "2026-06-10T11:30:00Z")) + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(shadow_root)
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_bridge))
    monkeypatch.delenv("AGENT_BRIDGE_ROOT", raising=False)

    rc = main(["--now", NOW_TEXT, "--producer", "codex-lead-1", "--json"])

    assert rc == 0
    art = json.loads(capsys.readouterr().out)
    entry = art["per_producer"]["codex-lead-1"]
    assert entry["status"] == "ok"
    assert entry["smallest_window_burn_per_hour"] == 1.0


def test_main_out_file_matches_stdout(tmp_path, capsys):
    path = _write(tmp_path, _busy("codex-lead-1", 2))
    out = tmp_path / "report.json"
    rc = main(
        ["--events", str(path), "--now", NOW_TEXT, "--producer", "codex-lead-1",
         "--json", "--out", str(out)]
    )
    assert rc == 0
    stdout_art = json.loads(capsys.readouterr().out)
    assert stdout_art == json.loads(out.read_text(encoding="utf-8"))


def test_main_read_only_does_not_touch_events(tmp_path):
    path = _write(tmp_path, _busy("codex-lead-1", 2))
    before = path.read_bytes()
    main(["--events", str(path), "--now", NOW_TEXT, "--producer", "codex-lead-1", "--json"])
    assert path.read_bytes() == before


def test_main_missing_events_exit_3(tmp_path):
    assert main(["--events", str(tmp_path / "absent.jsonl"), "--now", NOW_TEXT]) == 3


def test_main_invalid_args_exit_2(tmp_path):
    path = _write(tmp_path, _busy("codex-lead-1", 1))
    assert main(["--events", str(path), "--now", "junk"]) == 2
    assert main(["--events", str(path), "--now", NOW_TEXT, "--warn-events-per-hour", "0"]) == 2
    assert main(["--events", str(path), "--now", NOW_TEXT, "--reset-at", "junk"]) == 2
    # hard must exceed soft
    assert main(
        ["--events", str(path), "--now", NOW_TEXT, "--reset-at", RESET_TEXT,
         "--soft-budget", "10", "--hard-budget", "5"]
    ) == 2
    assert main(
        ["--events", str(path), "--now", NOW_TEXT, "--projection-window-hours", "nan"]
    ) == 2
