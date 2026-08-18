# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_operator_attention_digest.py"

sys.path.insert(0, str(ROOT))

from tools import build_operator_attention_digest as attention_module  # noqa: E402
from tools.build_operator_attention_digest import (  # noqa: E402
    OperatorAttentionDigestError,
    build_operator_attention_digest,
)


def _event(
    *,
    ts: str = "2026-06-14T05:00:00Z",
    agent: str = "codex-lead-1",
    to: str = "operator",
    event_type: str = "finding",
    task_id: str = "task-1",
    status: str = "operator_action_required",
    severity: str = "high",
    message: str = "wake file exists at C:\\secret\\bridge\\wake_claude-rco-2",
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "ts_utc": ts,
        "agent": agent,
        "to": to,
        "type": event_type,
        "task_id": task_id,
        "status": status,
        "severity": severity,
        "message": message,
        "payload": payload or {},
    }


def _now() -> datetime:
    return datetime(2026, 6, 14, 5, 30, tzinfo=timezone.utc)


def _events_file(path: Path, events: list[dict[str, object]]) -> Path:
    shared = path / "shared"
    shared.mkdir(parents=True)
    events_path = shared / "events.jsonl"
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    return events_path


def test_reports_operator_addressed_attention_without_paths() -> None:
    report = build_operator_attention_digest(
        events=[
            _event(
                payload={
                    "operator_action_required": True,
                    "do_not_emit_additional_wake_requests": True,
                    "pr": 1191,
                    "head": "a" * 40,
                }
            )
        ],
        now_utc=_now(),
    )

    assert report["ok"] is True
    assert report["decision"] == "operator_attention_digest"
    assert report["read_only"] is True
    assert report["push_delivery_attempted"] is False
    assert report["network_authority"] is False
    assert report["bridge_write_authority"] is False
    assert report["attention_count"] == 1
    item = report["items"][0]
    assert item["priority"] == "urgent"
    assert item["suggested_action"] == "verify_or_restart_target_session_watcher"
    assert item["operator_addressed"] is True
    assert item["pr"] == "1191"
    assert item["head"] == "a" * 40
    encoded = json.dumps(report, sort_keys=True)
    assert "C:\\secret" not in encoded
    assert "<redacted-path>" in encoded
    assert report["events_path_recorded"] is False
    assert report["local_paths_recorded"] is False


def test_live_wake_delivery_stall_is_urgent_synthetic_attention() -> None:
    report = build_operator_attention_digest(
        events=[
            _event(
                ts="2026-06-14T05:00:00Z",
                agent="operator",
                to="codex-lead-1",
                event_type="wake_request",
                task_id="bridge-follow-nudge-20260614",
                status="open",
                severity="",
                message="please read bridge",
            ),
            _event(
                ts="2026-06-14T05:12:00Z",
                agent="operator",
                to="codex-lead-1",
                event_type="wake_request",
                task_id="bridge-follow-nudge-20260614",
                status="open",
                severity="",
                message="please read bridge again",
            ),
        ],
        now_utc=_now(),
    )

    assert report["attention_count"] == 1
    assert report["wake_delivery_checked"] is True
    assert report["wake_delivery_stalled_count"] == 1
    item = report["items"][0]
    assert item["source_agent"] == "bridge-wake-delivery-monitor"
    assert item["priority"] == "urgent"
    assert item["rank_score"] > 100
    assert item["suggested_action"] == "verify_or_restart_target_session_watcher"
    assert item["target_agents"] == ["codex-lead-1"]
    assert item["stalled_wake_count"] == 1
    assert item["event_count"] == 2
    assert item["do_not_emit_additional_wake_requests"] is True
    assert "wake_delivery_stalled" in item["attention_reasons"]
    encoded = json.dumps(report, sort_keys=True)
    assert "C:\\" not in encoded


def test_live_wake_delivery_item_clears_after_target_activity() -> None:
    report = build_operator_attention_digest(
        events=[
            _event(
                ts="2026-06-14T05:00:00Z",
                agent="operator",
                to="codex-lead-1",
                event_type="wake_request",
                task_id="bridge-follow-nudge-20260614",
                status="open",
                severity="",
            ),
            _event(
                ts="2026-06-14T05:12:00Z",
                agent="operator",
                to="codex-lead-1",
                event_type="wake_request",
                task_id="bridge-follow-nudge-20260614",
                status="open",
                severity="",
            ),
            _event(
                ts="2026-06-14T05:20:00Z",
                agent="codex-lead-1",
                to="",
                event_type="status",
                task_id="codex-lead-active",
                status="active",
                severity="",
                message="read bridge and resumed",
            ),
        ],
        now_utc=_now(),
    )

    assert report["attention_count"] == 0
    assert report["wake_delivery_checked"] is True
    assert report["wake_delivery_stalled_count"] == 0
    assert report["items"] == []


def test_operator_wake_send_failed_is_operator_attention() -> None:
    report = build_operator_attention_digest(
        events=[
            _event(
                ts="2026-06-14T05:00:00Z",
                agent="operator",
                to="",
                event_type="message",
                task_id="wd/ops/stall-rescue-watch",
                status="wake_send_failed",
                severity="",
                message=(
                    "Keying 'codex-lead-1' failed (tab not found / UIA error): "
                    "Tab for agent 'codex-lead-1' not found. Tip: pass "
                    "-TitleMap 'codex-lead-1=<exact-title-substring>'."
                ),
            )
        ],
        now_utc=_now(),
        include_wake_delivery=False,
    )

    assert report["attention_count"] == 1
    item = report["items"][0]
    assert item["priority"] == "urgent"
    assert item["source_agent"] == "operator"
    assert item["status"] == "wake_send_failed"
    assert item["target_agents"] == ["codex-lead-1"]
    assert "wake_send_failed" in item["attention_reasons"]
    assert item["suggested_action"] == "repair_operator_wake_routing_or_title_map"
    assert "TitleMap" in item["message"]


def test_operator_wake_send_failed_clears_after_target_activity() -> None:
    report = build_operator_attention_digest(
        events=[
            _event(
                ts="2026-06-14T05:00:00Z",
                agent="operator",
                to="",
                event_type="message",
                task_id="wd/ops/stall-rescue-watch",
                status="wake_send_failed",
                severity="",
                message="Keying 'codex-lead-1' failed (tab not found).",
            ),
            _event(
                ts="2026-06-14T05:04:00Z",
                agent="codex-lead-1",
                to="",
                event_type="decision",
                task_id="lead-active",
                status="working",
                severity="",
                message="lead session active again",
            ),
        ],
        now_utc=_now(),
        include_wake_delivery=False,
    )

    assert report["attention_count"] == 0
    assert report["items"] == []


def test_later_terminal_event_closes_operator_attention() -> None:
    report = build_operator_attention_digest(
        events=[
            _event(task_id="task-close", payload={"pr": 1192}),
            _event(
                ts="2026-06-14T05:10:00Z",
                agent="driver",
                to="operator",
                event_type="done",
                task_id="different-task",
                status="merged",
                severity="",
                message="merged",
                payload={"pr": 1192},
            ),
        ],
        now_utc=_now(),
    )

    assert report["attention_count"] == 0
    assert report["items"] == []


def test_repeated_operator_attention_uses_latest_event_and_count() -> None:
    report = build_operator_attention_digest(
        events=[
            _event(
                ts="2026-06-14T05:00:00Z",
                task_id="bridge-follow-nudge-20260614",
                status="open",
                severity="major",
            ),
            _event(
                ts="2026-06-14T05:12:00Z",
                task_id="bridge-follow-nudge-20260614",
                status="open",
                severity="major",
                message="operator action required",
            ),
        ],
        now_utc=_now(),
    )

    assert report["attention_count"] == 1
    item = report["items"][0]
    assert item["event_count"] == 2
    assert item["ts_utc"] == "2026-06-14T05:12:00Z"
    assert item["first_ts_utc"] == "2026-06-14T05:00:00Z"
    assert item["age_minutes"] == 18.0


def test_ignores_non_operator_targets_and_operator_authored_events() -> None:
    report = build_operator_attention_digest(
        events=[
            _event(to="codex-tools-1", task_id="not-operator"),
            _event(agent="operator", to="codex-tools-1", task_id="from-operator"),
            _event(
                event_type="done",
                task_id="closed",
                status="merged_with_magma_receipt",
                severity="major",
            ),
            _event(
                event_type="claim",
                task_id="active-claim",
                status="active",
                severity="major",
            ),
        ],
        now_utc=_now(),
    )

    assert report["attention_count"] == 0


def test_age_filters_and_max_items() -> None:
    report = build_operator_attention_digest(
        events=[
            _event(ts="2026-06-14T05:28:00Z", task_id="young"),
            _event(ts="2026-06-14T05:00:00Z", task_id="old-1", severity=""),
            _event(ts="2026-06-14T04:50:00Z", task_id="old-2", severity="major"),
        ],
        min_age_minutes=10,
        max_items=1,
        now_utc=_now(),
    )

    assert report["attention_count"] == 1
    assert report["items"][0]["task_id"] == "old-2"


def test_default_now_ignores_future_attention_without_hiding_current(
    monkeypatch,
) -> None:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _now()

    monkeypatch.setattr(attention_module, "datetime", FixedDatetime)

    report = build_operator_attention_digest(
        events=[
            _event(ts="2026-06-14T05:00:00Z", task_id="current"),
            _event(ts="2099-01-01T00:00:00Z", task_id="future"),
        ],
        include_wake_delivery=False,
    )

    assert report["attention_count"] == 1
    assert report["items"][0]["task_id"] == "current"


@pytest.mark.parametrize("bad_ts", ["2099-01-01T00:00:00Z", "not-a-time"])
def test_invalid_or_future_same_key_attention_cannot_hide_current(
    bad_ts: str,
) -> None:
    report = build_operator_attention_digest(
        events=[
            _event(ts="2026-06-14T05:00:00Z", task_id="same-task"),
            _event(ts=bad_ts, task_id="same-task"),
        ],
        now_utc=_now(),
        include_wake_delivery=False,
    )

    assert report["attention_count"] == 1
    assert report["items"][0]["ts_utc"] == "2026-06-14T05:00:00Z"
    assert report["items"][0]["event_count"] == 1


def test_backdated_repeat_cannot_expire_newer_operator_attention() -> None:
    report = build_operator_attention_digest(
        events=[
            _event(ts="2026-06-14T05:20:00Z", task_id="same-task"),
            _event(ts="2026-06-13T05:20:00Z", task_id="same-task"),
        ],
        now_utc=_now(),
        include_wake_delivery=False,
    )

    assert report["attention_count"] == 1
    item = report["items"][0]
    assert item["ts_utc"] == "2026-06-14T05:20:00Z"
    assert item["event_count"] == 2
    assert item["age_minutes"] == 10.0


@pytest.mark.parametrize("closure_ts", ["2026-06-13T05:00:00Z", "!"])
def test_later_appended_terminal_closes_regardless_of_event_clock(
    closure_ts: str,
) -> None:
    report = build_operator_attention_digest(
        events=[
            _event(task_id="task-close"),
            _event(
                ts=closure_ts,
                agent="driver",
                to="operator",
                event_type="done",
                task_id="task-close",
                status="merged",
                severity="",
            ),
        ],
        now_utc=_now(),
        include_wake_delivery=False,
    )

    assert report["attention_count"] == 0


@pytest.mark.parametrize("malformed_pr", [True, 1208.5, "not-pr"])
def test_malformed_pr_does_not_create_cross_task_closure(
    malformed_pr: object,
) -> None:
    report = build_operator_attention_digest(
        events=[
            _event(task_id="attention", payload={"pr": malformed_pr}),
            _event(
                ts="2026-06-14T05:10:00Z",
                agent="driver",
                to="operator",
                event_type="done",
                task_id="different-task",
                status="merged",
                severity="",
                payload={"pr": malformed_pr},
            ),
        ],
        now_utc=_now(),
        include_wake_delivery=False,
    )

    assert report["attention_count"] == 1
    assert report["items"][0]["task_id"] == "attention"
    assert "pr" not in report["items"][0]


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"min_age_minutes": -1}, "min_age_minutes must be finite and non-negative"),
        (
            {"min_age_minutes": float("nan")},
            "min_age_minutes must be finite and non-negative",
        ),
        ({"max_age_hours": float("inf")}, "max_age_hours must be finite"),
        ({"max_items": 0}, "max_items must be positive"),
    ],
)
def test_rejects_invalid_limits(kwargs: dict[str, object], error: str) -> None:
    with pytest.raises(OperatorAttentionDigestError) as exc:
        build_operator_attention_digest(events=[], **kwargs)

    assert error in exc.value.report["errors"]


def test_non_terminal_peer_pass_does_not_close_operator_attention() -> None:
    report = build_operator_attention_digest(
        events=[
            _event(
                task_id="operator-gated-pr",
                status="operator_signature_required",
                severity="major",
                payload={"pr": 1195},
            ),
            _event(
                ts="2026-06-14T05:10:00Z",
                agent="claude-rco-1",
                to="codex-tools-1",
                event_type="message",
                task_id="operator-gated-pr",
                status="rco_content_pass_pending_ci_operator_gated",
                severity="info",
                message="RCO content pass pending CI; operator signature still required.",
                payload={"pr": 1195},
            ),
        ],
        now_utc=_now(),
    )

    assert report["attention_count"] == 1
    assert report["items"][0]["task_id"] == "operator-gated-pr"


def test_closed_status_closes_operator_attention() -> None:
    report = build_operator_attention_digest(
        events=[
            _event(task_id="operator-gated-pr", payload={"pr": 1195}),
            _event(
                ts="2026-06-14T05:10:00Z",
                agent="operator",
                to="codex-lead-1",
                event_type="decision",
                task_id="operator-gated-pr",
                status="approved",
                severity="",
                message="approved",
                payload={"pr": 1195},
            ),
        ],
        now_utc=_now(),
    )

    assert report["attention_count"] == 0


def test_cli_json_is_path_free(tmp_path: Path) -> None:
    events_path = _events_file(
        tmp_path,
        [
            _event(
                message=f"operator action needed for {tmp_path}\\private\\file.txt",
                payload={"operator_action_required": True},
            )
        ],
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--events",
            str(events_path),
            "--now",
            "2026-06-14T05:30:00Z",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["attention_count"] == 1
    encoded = json.dumps(payload, sort_keys=True)
    assert str(tmp_path) not in encoded
    assert "private" not in encoded
    assert payload["items"][0]["push_delivery_attempted"] is False


@pytest.mark.parametrize(
    "flag",
    ["--min-age-minutes", "--max-age-hours"],
)
def test_cli_rejects_non_finite_numbers(flag: str) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            flag,
            "NaN",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    encoded = json.dumps(payload, sort_keys=True)
    assert "NaN" not in encoded
