# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import json

from tools.run_release_soak_log_audit import build_report, main


def test_soak_log_audit_passes_clean_explicit_source(tmp_path) -> None:
    source = tmp_path / "soak.log"
    source.write_text(
        "2026-05-22T12:00:00Z INFO 36 cycles complete, 0 errors, no silent failures\n",
        encoding="utf-8",
    )

    report = build_report(
        [source],
        started_at_utc=dt.datetime(2026, 5, 10, tzinfo=dt.UTC),
        ended_at_utc=dt.datetime(2026, 5, 22, 12, 0, tzinfo=dt.UTC),
    )

    assert report["audit_result"] == "pass"
    assert report["silent_failure_count"] == 0
    assert report["error_count"] == 0
    assert report["error_log_clean"] is True
    assert report["undated_record_count"] == 0
    assert report["source_files"] == [source.as_posix()]
    assert report["source_hashes"][source.as_posix()].startswith("sha256:")


def test_soak_log_audit_blocks_errors_in_jsonl_source(tmp_path) -> None:
    source = tmp_path / "incident_log.jsonl"
    source.write_text(
        json.dumps({"ts": "2026-05-22T12:00:00Z", "error_count": 1}) + "\n",
        encoding="utf-8",
    )

    report = build_report([source])

    assert report["audit_result"] == "blocked"
    assert report["error_count"] == 1
    assert "errors_detected" in report["blockers"]


def test_soak_log_audit_ignores_timestamped_jsonl_before_soak_window(tmp_path) -> None:
    source = tmp_path / "error_log.jsonl"
    source.write_text(
        json.dumps({
            "ts": "2026-04-28T01:00:00Z",
            "severity": "recoverable",
            "summary": "Known shell failure mode before v3.12.0 soak.",
            "fatal": False,
        })
        + "\n",
        encoding="utf-8",
    )

    report = build_report(
        [source],
        started_at_utc=dt.datetime(2026, 5, 10, tzinfo=dt.UTC),
        ended_at_utc=dt.datetime(2026, 5, 22, 12, 0, tzinfo=dt.UTC),
    )

    assert report["audit_result"] == "pass"
    assert report["error_count"] == 0
    assert report["silent_failure_count"] == 0


def test_soak_log_audit_still_blocks_timestamped_jsonl_inside_window(tmp_path) -> None:
    source = tmp_path / "error_log.jsonl"
    source.write_text(
        json.dumps({
            "ts": "2026-05-22T11:00:00Z",
            "severity": "recoverable",
            "summary": "Runtime failure during v3.12.0 soak.",
            "fatal": False,
        })
        + "\n",
        encoding="utf-8",
    )

    report = build_report(
        [source],
        started_at_utc=dt.datetime(2026, 5, 10, tzinfo=dt.UTC),
        ended_at_utc=dt.datetime(2026, 5, 22, 12, 0, tzinfo=dt.UTC),
    )

    assert report["audit_result"] == "blocked"
    assert report["error_count"] == 1
    assert "errors_detected" in report["blockers"]


def test_soak_log_audit_does_not_skip_nested_in_window_events(tmp_path) -> None:
    source = tmp_path / "wrapped_log.jsonl"
    source.write_text(
        json.dumps({
            "created_at": "2026-04-28T01:00:00Z",
            "batch": [
                {
                    "ts": "2026-05-22T11:00:00Z",
                    "error_count": 1,
                }
            ],
        })
        + "\n",
        encoding="utf-8",
    )

    report = build_report(
        [source],
        started_at_utc=dt.datetime(2026, 5, 10, tzinfo=dt.UTC),
        ended_at_utc=dt.datetime(2026, 5, 22, 12, 0, tzinfo=dt.UTC),
    )

    assert report["audit_result"] == "blocked"
    assert report["error_count"] == 1
    assert "errors_detected" in report["blockers"]


def test_soak_log_audit_scans_undated_jsonl_fail_closed(tmp_path) -> None:
    source = tmp_path / "incident_log.jsonl"
    source.write_text(
        json.dumps({"summary": "Runtime failure without timestamp."}) + "\n",
        encoding="utf-8",
    )

    report = build_report([source])

    assert report["audit_result"] == "blocked"
    assert report["error_count"] == 1
    assert report["undated_record_count"] == 1
    assert "errors_detected" in report["blockers"]
    assert "undated_records_detected" in report["blockers"]


def test_soak_log_audit_filters_timestamped_text_lines(tmp_path) -> None:
    source = tmp_path / "soak.log"
    source.write_text(
        "\n".join([
            "2026-04-28T00:00:00Z ERROR old pre-soak issue",
            "2026-05-22T12:00:00Z INFO 36 cycles complete, 0 errors, no silent failures",
        ]),
        encoding="utf-8",
    )

    report = build_report(
        [source],
        started_at_utc=dt.datetime(2026, 5, 10, tzinfo=dt.UTC),
        ended_at_utc=dt.datetime(2026, 5, 22, 12, 0, tzinfo=dt.UTC),
    )

    assert report["audit_result"] == "pass"
    assert report["error_count"] == 0


def test_soak_log_audit_blocks_plural_error_text(tmp_path) -> None:
    source = tmp_path / "soak.log"
    source.write_text(
        "2026-05-22T12:00:00Z WARN 2 errors detected during soak\n",
        encoding="utf-8",
    )

    report = build_report([source])

    assert report["audit_result"] == "blocked"
    assert report["error_count"] == 1
    assert "errors_detected" in report["blockers"]


def test_soak_log_audit_blocks_missing_sources(tmp_path) -> None:
    report = build_report([tmp_path / "missing.log"])

    assert report["audit_result"] == "blocked"
    assert report["silent_failure_count"] == 0
    assert "source_missing:" in report["blockers"][0]


def test_soak_log_audit_blocks_malformed_jsonl_source(tmp_path) -> None:
    source = tmp_path / "incident_log.jsonl"
    source.write_text("{not-json}\n", encoding="utf-8")

    report = build_report([source])

    assert report["audit_result"] == "blocked"
    assert report["silent_failure_count"] == 0
    assert report["error_count"] == 0
    assert "source_unreadable:" in report["blockers"][0]


def test_soak_log_audit_cli_writes_blocked_report_without_sources(tmp_path) -> None:
    output = tmp_path / "audit.json"

    rc = main(["--output", str(output)])

    assert rc == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["audit_result"] == "blocked"
    assert report["source_files"] == []
    assert "source_files_missing" in report["blockers"]
