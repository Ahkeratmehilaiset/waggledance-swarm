# SPDX-License-Identifier: BUSL-1.1
"""Unit tests for tools/producer_budget_probe.py.

All fixtures are synthetic events.jsonl files written to tmp_path; the
probe is exercised both through the pure helper and the CLI main().
Timestamps mirror the live bridge shape (7 fractional digits + Z).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.producer_budget_probe import (
    CLAIM_GATES,
    main,
    parse_ts,
    probe_producer_budget,
    read_events_tolerant,
)

NOW_TEXT = "2026-06-10T12:00:00Z"
NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def _event(agent: str, ts_utc: str, type_: str = "decision") -> dict:
    return {
        "ts_utc": ts_utc,
        "agent": agent,
        "type": type_,
        "task_id": "task-x",
        "status": "ok",
        "message": "m",
    }


def _write_events(tmp_path: Path, events: list) -> Path:
    p = tmp_path / "events.jsonl"
    p.write_text(
        "\n".join(json.dumps(e) if isinstance(e, dict) else e for e in events)
        + "\n",
        encoding="utf-8",
    )
    return p


# --- timestamp parsing -------------------------------------------------------


def test_parse_ts_live_bridge_shape_seven_fraction_digits():
    ts = parse_ts("2026-06-10T07:18:28.5239091Z")
    assert ts == datetime(2026, 6, 10, 7, 18, 28, 523909, tzinfo=timezone.utc)


def test_parse_ts_naive_is_utc_and_garbage_is_none():
    assert parse_ts("2026-06-10T07:00:00") == datetime(
        2026, 6, 10, 7, 0, 0, tzinfo=timezone.utc
    )
    assert parse_ts("not-a-timestamp") is None
    assert parse_ts("") is None


# --- window counting ---------------------------------------------------------


def test_window_counting_splits_heartbeat_from_substantive():
    events = [
        _event("codex-lead-1", "2026-06-10T11:30:00Z"),                # in 1h
        _event("codex-lead-1", "2026-06-10T11:45:00Z", "heartbeat"),   # in 1h
        _event("codex-lead-1", "2026-06-10T08:00:00Z"),                # in 6h only
        _event("codex-lead-1", "2026-06-01T00:00:00Z"),                # outside all
        _event("codex-tools-1", "2026-06-10T11:59:00Z"),
        _event("claude-rco-1", "2026-06-10T11:59:00Z"),                # not a producer
    ]
    result = probe_producer_budget(
        events=events,
        producers=("codex-lead-1", "codex-tools-1"),
        now=NOW,
        window_hours=(1.0, 6.0),
    )
    lead = result["per_producer"]["codex-lead-1"]
    assert lead["total_events"] == 4
    assert lead["windows"]["1h"]["total_events"] == 2
    assert lead["windows"]["1h"]["heartbeat_events"] == 1
    assert lead["windows"]["1h"]["substantive_events"] == 1
    assert lead["windows"]["1h"]["substantive_events_per_hour"] == 1.0
    assert lead["windows"]["6h"]["substantive_events"] == 2
    tools = result["per_producer"]["codex-tools-1"]
    assert tools["windows"]["1h"]["substantive_events"] == 1
    assert "claude-rco-1" not in result["per_producer"]


def test_burn_rate_normalized_per_hour():
    events = [
        _event("codex-lead-1", f"2026-06-10T{h:02d}:00:00Z") for h in range(6, 12)
    ]
    result = probe_producer_budget(
        events=events,
        producers=("codex-lead-1",),
        now=NOW,
        window_hours=(24.0,),
    )
    bucket = result["per_producer"]["codex-lead-1"]["windows"]["24h"]
    assert bucket["substantive_events"] == 6
    assert bucket["substantive_events_per_hour"] == 0.25


def test_liveness_gap_and_never_seen():
    events = [_event("codex-lead-1", "2026-06-10T11:00:00Z")]
    result = probe_producer_budget(
        events=events,
        producers=("codex-lead-1", "codex-tools-1"),
        now=NOW,
    )
    lead = result["per_producer"]["codex-lead-1"]
    assert lead["minutes_since_last_event"] == 60.0
    assert lead["last_event_ts_utc"].startswith("2026-06-10T11:00:00")
    tools = result["per_producer"]["codex-tools-1"]
    assert tools["minutes_since_last_event"] is None
    assert tools["last_event_ts_utc"] is None


def test_future_and_unparseable_timestamps_surfaced_not_counted():
    events = [
        _event("codex-lead-1", "2026-06-10T13:00:00Z"),  # future vs NOW
        _event("codex-lead-1", "garbage"),
        _event("codex-lead-1", "2026-06-10T11:30:00Z"),
    ]
    result = probe_producer_budget(
        events=events,
        producers=("codex-lead-1",),
        now=NOW,
        window_hours=(1.0,),
    )
    lead = result["per_producer"]["codex-lead-1"]
    assert lead["total_events"] == 3
    assert lead["future_ts_events"] == 1
    assert lead["unparseable_ts_events"] == 1
    assert lead["windows"]["1h"]["total_events"] == 1


# --- advisory threshold ------------------------------------------------------


def test_warn_threshold_flags_producer_on_smallest_window():
    events = [
        _event("codex-lead-1", f"2026-06-10T11:{m:02d}:00Z") for m in range(10, 40)
    ]
    result = probe_producer_budget(
        events=events,
        producers=("codex-lead-1", "codex-tools-1"),
        now=NOW,
        window_hours=(1.0, 24.0),
        warn_events_per_hour=10.0,
    )
    assert result["warned_producers"] == ["codex-lead-1"]


def test_no_warn_when_threshold_not_given():
    events = [
        _event("codex-lead-1", f"2026-06-10T11:{m:02d}:00Z") for m in range(10, 40)
    ]
    result = probe_producer_budget(
        events=events, producers=("codex-lead-1",), now=NOW
    )
    assert result["warned_producers"] == []


# --- reset projection --------------------------------------------------------


def test_reset_projection_uses_basis_window_rate():
    # 6 substantive events in the trailing 24h -> 0.25/h; reset in 48h -> 12.0
    events = [
        _event("codex-lead-1", f"2026-06-10T{h:02d}:00:00Z") for h in range(6, 12)
    ]
    result = probe_producer_budget(
        events=events,
        producers=("codex-lead-1",),
        now=NOW,
        window_hours=(24.0,),
        reset_at=datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc),
        projection_window_hours=24.0,
    )
    assert result["hours_until_reset"] == 48.0
    assert result["projection_window_hours"] == 24.0
    lead = result["per_producer"]["codex-lead-1"]
    assert lead["projected_substantive_events_to_reset"] == 12.0


def test_reset_in_past_clamps_to_zero():
    events = [_event("codex-lead-1", "2026-06-10T11:00:00Z")]
    result = probe_producer_budget(
        events=events,
        producers=("codex-lead-1",),
        now=NOW,
        window_hours=(24.0,),
        reset_at=datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc),
        projection_window_hours=24.0,
    )
    assert result["hours_until_reset"] == 0.0
    lead = result["per_producer"]["codex-lead-1"]
    assert lead["projected_substantive_events_to_reset"] == 0.0


def test_no_reset_given_projection_fields_null():
    result = probe_producer_budget(events=[], producers=("codex-lead-1",), now=NOW)
    assert result["reset_at_utc"] is None
    assert result["hours_until_reset"] is None
    assert result["projection_window_hours"] is None
    lead = result["per_producer"]["codex-lead-1"]
    assert lead["projected_substantive_events_to_reset"] is None


def test_projection_basis_must_be_reported_window():
    import pytest

    with pytest.raises(ValueError):
        probe_producer_budget(
            events=[],
            producers=("codex-lead-1",),
            now=NOW,
            window_hours=(24.0,),
            reset_at=NOW,
            projection_window_hours=12.0,
        )


def test_main_reset_projection_cli(tmp_path, capsys):
    path = _write_events(
        tmp_path,
        [_event("codex-lead-1", f"2026-06-10T{h:02d}:00:00Z") for h in range(6, 12)],
    )
    rc = main(
        [
            "--events", str(path),
            "--now", NOW_TEXT,
            "--window-hours", "24",
            "--reset-at", "2026-06-12T12:00:00Z",
            "--projection-window-hours", "24",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hours_until_reset"] == 48.0
    assert (
        payload["per_producer"]["codex-lead-1"][
            "projected_substantive_events_to_reset"
        ]
        == 12.0
    )


def test_main_exit_2_bad_reset_args(tmp_path):
    path = _write_events(tmp_path, [_event("codex-lead-1", "2026-06-10T11:00:00Z")])
    assert main(["--events", str(path), "--now", NOW_TEXT, "--reset-at", "junk"]) == 2
    # basis window not among reported windows
    assert main(
        [
            "--events", str(path),
            "--now", NOW_TEXT,
            "--window-hours", "24",
            "--reset-at", "2026-06-12T12:00:00Z",
            "--projection-window-hours", "12",
        ]
    ) == 2
    assert main(
        [
            "--events", str(path),
            "--now", NOW_TEXT,
            "--reset-at", "2026-06-12T12:00:00Z",
            "--projection-window-hours", "nan",
        ]
    ) == 2


# --- claim gates / advisory framing -----------------------------------------


def test_all_claim_gates_emitted_false():
    result = probe_producer_budget(events=[], producers=("codex-lead-1",), now=NOW)
    assert CLAIM_GATES
    for gate in CLAIM_GATES:
        assert result[gate] is False
    assert result["advisory_only"] is True
    assert result["read_only"] is True


# --- tolerant reader ---------------------------------------------------------


def test_read_events_tolerant_counts_malformed(tmp_path):
    path = _write_events(
        tmp_path,
        [
            _event("codex-lead-1", "2026-06-10T11:30:00Z"),
            "{not json",
            json.dumps(["a", "list"]),
            "",
            _event("codex-lead-1", "2026-06-10T11:31:00Z"),
        ],
    )
    events, malformed = read_events_tolerant(path)
    assert len(events) == 2
    assert malformed == 2


# --- CLI ----------------------------------------------------------------------


def test_main_json_output_read_only(tmp_path, capsys):
    path = _write_events(
        tmp_path, [_event("codex-lead-1", "2026-06-10T11:30:00Z")]
    )
    before = path.read_bytes()
    rc = main(
        ["--events", str(path), "--now", NOW_TEXT, "--json"]
    )
    assert rc == 0
    assert path.read_bytes() == before  # read-only: file untouched
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["per_producer"]["codex-lead-1"]["total_events"] == 1


def test_main_uses_runtime_bridge_root_env_by_default(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_bridge = tmp_path / "runtime" / ".agent-bridge"
    runtime_events = runtime_bridge / "shared" / "events.jsonl"
    runtime_events.parent.mkdir(parents=True)
    runtime_events.write_text(
        json.dumps(_event("codex-lead-1", "2026-06-10T11:30:00Z")) + "\n",
        encoding="utf-8",
    )

    shadow_root = tmp_path / "shadow"
    shadow_events = shadow_root / ".agent-bridge" / "shared" / "events.jsonl"
    shadow_events.parent.mkdir(parents=True)
    shadow_events.write_text(
        json.dumps(_event("codex-tools-1", "2026-06-10T11:30:00Z")) + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(shadow_root)
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_bridge))
    monkeypatch.delenv("AGENT_BRIDGE_ROOT", raising=False)

    rc = main(["--now", NOW_TEXT, "--window-hours", "1", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["per_producer"]["codex-lead-1"]["total_events"] == 1
    assert payload["per_producer"]["codex-tools-1"]["total_events"] == 0


def test_main_exit_4_on_warn(tmp_path):
    path = _write_events(
        tmp_path,
        [
            _event("codex-lead-1", f"2026-06-10T11:{m:02d}:00Z")
            for m in range(10, 40)
        ],
    )
    rc = main(
        [
            "--events", str(path),
            "--now", NOW_TEXT,
            "--warn-events-per-hour", "10",
            "--json",
        ]
    )
    assert rc == 4


def test_main_exit_3_missing_events_file(tmp_path):
    rc = main(["--events", str(tmp_path / "absent.jsonl"), "--now", NOW_TEXT])
    assert rc == 3


def test_main_exit_2_invalid_args(tmp_path):
    path = _write_events(tmp_path, [_event("codex-lead-1", NOW_TEXT)])
    assert main(["--events", str(path), "--now", "not-a-time"]) == 2
    assert main(["--events", str(path), "--now", NOW_TEXT, "--producer", "BAD ID"]) == 2
    assert main(
        ["--events", str(path), "--now", NOW_TEXT, "--window-hours", "-1"]
    ) == 2
    assert main(
        ["--events", str(path), "--now", NOW_TEXT, "--window-hours", "nan"]
    ) == 2
    assert main(
        ["--events", str(path), "--now", NOW_TEXT, "--warn-events-per-hour", "0"]
    ) == 2
