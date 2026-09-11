# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tools import run_release_soak_log_audit as audit
from tools.run_release_soak_log_audit import (
    CONTRACT_VERSION,
    ROLE_COVERAGE,
    ROLE_DIAGNOSTIC,
    SCHEMA_VERSION,
    build_bound_report,
    build_report,
    is_healthy_coverage_record,
    main,
    validate_coverage_record,
)


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


# --- bound producer (v2 field set) -------------------------------------------

LEGACY_KEYS = frozenset({
    "schema_version",
    "target_version",
    "audit_id",
    "command",
    "source_files",
    "source_hashes",
    "source_file_count",
    "started_at_utc",
    "ended_at_utc",
    "silent_failure_count",
    "error_count",
    "undated_record_count",
    "error_log_clean",
    "blockers",
    "audit_result",
})
COVERAGE_REL = "docs/runs/release_soak_evidence/v3.12.0_soak_heartbeat.jsonl"
ERROR_LOG_REL = "docs/runs/error_log.jsonl"
HISTORY_REL = "docs/runs/release_soak_evidence/v3.12.0_history.jsonl"
LOCK_TEXT = "pytest==9.0.0\nhypothesis==6.0.0\n"
LOCK_DIGEST = "sha256:" + hashlib.sha256(LOCK_TEXT.encode("utf-8")).hexdigest()
T0 = dt.datetime(2026, 9, 15, tzinfo=dt.UTC)
T1 = T0 + dt.timedelta(hours=336)
GENERATED = T1 + dt.timedelta(hours=1)
LEGACY_ERROR_LOG_RECORD = {
    "ts": "2026-04-28T00:00:00Z",
    "class": "boot",
    "fatal": False,
    "section": "shell",
    "severity": "low",
    "summary": "pre-soak note",
}
HISTORY_ENVELOPE = {
    "started_at_utc": "2026-05-10T00:00:00Z",
    "ended_at_utc": "2026-05-24T00:00:00Z",
    "result": "pass",
}


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, f"git {' '.join(args)} failed: {completed.stderr.strip()}"
    return completed.stdout.strip()


def _write_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _heartbeat(instant: dt.datetime, seq: int, commit: str, **overrides: object) -> dict:
    record: dict = {
        "ts_utc": instant.isoformat().replace("+00:00", "Z"),
        "kind": "soak_heartbeat",
        "source_commit": commit,
        "lock_digest": LOCK_DIGEST,
        "seq": seq,
        "state": "ok",
        "probe_status_http": 200,
        "probe_latency_ms": 143,
        "queries_sent": 12,
        "queries_ok": 12,
        "owner_pid": 4242,
        "runner": "fresh-soak-owned-v1",
    }
    record.update(overrides)
    return record


def _stream(commit: str, *, step_hours: int = 1, hole: tuple[int, int] | None = None) -> str:
    lines = []
    seq = 0
    hour = 0
    while hour <= 336:
        if hole is None or not (hole[0] <= hour < hole[1]):
            lines.append(json.dumps(_heartbeat(T0 + dt.timedelta(hours=hour), seq, commit)))
            seq += 1
        hour += step_hours
    return "\n".join(lines) + "\n"


@pytest.fixture
def subject_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for key, value in (
        ("user.email", "soak@example.com"),
        ("user.name", "soak"),
        ("core.autocrlf", "false"),
        ("core.longpaths", "true"),
        ("commit.gpgsign", "false"),
    ):
        _git(repo, "config", key, value)
    _write_lf(repo / "requirements.lock.txt", LOCK_TEXT)
    _write_lf(repo / ERROR_LOG_REL, json.dumps(LEGACY_ERROR_LOG_RECORD) + "\n")
    _write_lf(repo / HISTORY_REL, json.dumps(HISTORY_ENVELOPE) + "\n")
    _write_lf(repo / COVERAGE_REL, "")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "subject S")
    return repo, _git(repo, "rev-parse", "HEAD")


def _bound(repo: Path, commit: str, **overrides: object) -> dict:
    kwargs: dict = {
        "source_root": repo,
        "source_commit": commit,
        "coverage_sources": [Path(COVERAGE_REL)],
        "started_at_utc": T0,
        "ended_at_utc": T1,
        "generated_at": GENERATED,
    }
    kwargs.update(overrides)
    sources = kwargs.pop("sources", [Path(ERROR_LOG_REL), Path(HISTORY_REL), Path(COVERAGE_REL)])
    return build_bound_report(sources, **kwargs)


def test_bound_report_clean_coverage_stream_passes(subject_repo) -> None:
    repo, commit = subject_repo
    _write_lf(repo / COVERAGE_REL, _stream(commit))

    report = _bound(repo, commit)

    assert report["blockers"] == []
    assert report["audit_result"] == "pass"
    assert report["error_log_clean"] is True
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["contract_version"] == CONTRACT_VERSION
    assert report["source_commit"] == commit
    assert report["source_tree"] == _git(repo, "rev-parse", "HEAD^{tree}")
    assert report["generated_at"] == "2026-09-29T01:00:00Z"
    assert report["started_at_utc"] == "2026-09-15T00:00:00Z"
    assert report["ended_at_utc"] == "2026-09-29T00:00:00Z"
    assert report["window_hours"] == 336.0
    assert report["required_window_hours"] == 336
    assert report["source_roles"] == {
        ERROR_LOG_REL: ROLE_DIAGNOSTIC,
        HISTORY_REL: ROLE_DIAGNOSTIC,
        COVERAGE_REL: ROLE_COVERAGE,
    }
    assert report["coverage_sources"] == [COVERAGE_REL]
    assert report["lock_path"] == "requirements.lock.txt"
    assert report["lock_digest"] == LOCK_DIGEST
    assert report["lock_blob"] == _git(repo, "rev-parse", "HEAD:requirements.lock.txt")
    coverage_binding = report["raw_log_binding"][COVERAGE_REL]
    assert coverage_binding["append_only"] is True
    assert coverage_binding["subject_line_count"] == 0
    assert coverage_binding["appended_line_count"] == 337
    for key in (ERROR_LOG_REL, HISTORY_REL):
        assert report["raw_log_binding"][key]["append_only"] is True
        assert report["raw_log_binding"][key]["appended_line_count"] == 0
    stats = report["coverage"][COVERAGE_REL]
    assert stats["records_total"] == 337
    assert stats["records_healthy"] == 337
    assert stats["records_nonhealthy"] == 0
    assert stats["records_in_window"] == 337
    assert stats["first_in_window"] == "2026-09-15T00:00:00Z"
    assert stats["last_in_window"] == "2026-09-29T00:00:00Z"
    assert stats["max_gap_seconds"] == 3600
    assert report["worktree"]["head"] == commit
    assert report["worktree"]["changed_tracked_paths"] == [COVERAGE_REL]
    assert report["source_hashes"][COVERAGE_REL] == (
        "sha256:" + hashlib.sha256(_stream(commit).encode("utf-8")).hexdigest()
    )
    assert report["source_file_count"] == 3


def test_legacy_report_keys_unchanged_without_source_commit(tmp_path) -> None:
    source = tmp_path / "incident_log.jsonl"
    source.write_text(
        json.dumps({"ts": "2026-05-22T12:00:00Z", "summary": "cycle complete"}) + "\n",
        encoding="utf-8",
    )

    report = build_report([source])
    output = tmp_path / "audit.json"
    rc = main(["--output", str(output), "--source", str(source)])

    assert frozenset(report) == LEGACY_KEYS
    assert rc == 0
    assert frozenset(json.loads(output.read_text(encoding="utf-8"))) == LEGACY_KEYS


def test_cli_bound_only_flags_require_source_commit(tmp_path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--output", str(tmp_path / "audit.json"), "--coverage-source", COVERAGE_REL])
    assert excinfo.value.code == 2


def test_scan_source_api_is_stable(tmp_path) -> None:
    source = tmp_path / "log.jsonl"
    source.write_text(json.dumps({"ts": "2026-09-16T00:00:00Z", "error_count": 2}) + "\n")

    counts = audit._scan_source(source, started_at_utc=T0, ended_at_utc=T1)

    assert counts == (0, 2, 0)


def test_validate_coverage_record_rules() -> None:
    commit = "a" * 40
    healthy = _heartbeat(T0, 0, commit)
    assert validate_coverage_record(healthy, source_commit=commit, lock_digest=LOCK_DIGEST) is None
    assert is_healthy_coverage_record(healthy) is True
    nested = dict(healthy, probe={"status_http": 200})
    assert validate_coverage_record(nested) == "nested_value:probe"
    two_keys = dict(healthy, started_at_utc=healthy["ts_utc"])
    assert validate_coverage_record(two_keys) == "timestamp_key_count:2"
    assert validate_coverage_record(dict(healthy, failures=0)) == "counted_key:failures"
    assert validate_coverage_record(dict(healthy, state="failed")) == "scanner_word:state"
    naive = dict(healthy, ts_utc="2026-09-15T00:00:00")
    assert validate_coverage_record(naive) == "timestamp_not_utc_zero"
    other_key = {key: value for key, value in healthy.items() if key != "ts_utc"}
    other_key["ts"] = healthy["ts_utc"]
    assert validate_coverage_record(other_key) == "timestamp_key:ts"
    assert validate_coverage_record(dict(healthy, owner_pid=None)) == "null_value:owner_pid"
    assert (
        validate_coverage_record(healthy, source_commit="b" * 40, lock_digest=LOCK_DIGEST)
        == "source_commit_mismatch"
    )
    assert (
        validate_coverage_record(healthy, source_commit=commit, lock_digest="sha256:00")
        == "lock_digest_mismatch"
    )
    assert validate_coverage_record(dict(healthy, seq=-1)) == "seq_invalid"
    missing = {key: value for key, value in healthy.items() if key != "lock_digest"}
    assert validate_coverage_record(missing) == "missing_key:lock_digest"
    probe_error = _heartbeat(
        T0, 1, commit, kind="soak_probe_error", state="probe_unavailable", error_count=1
    )
    assert validate_coverage_record(probe_error, source_commit=commit, lock_digest=LOCK_DIGEST) is None
    assert is_healthy_coverage_record(probe_error) is False
    assert validate_coverage_record(dict(probe_error, error_count=-1)) == "error_count_invalid"
    assert validate_coverage_record("not a record") == "record_not_object"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda commit: json.dumps(dict(_heartbeat(T0, 0, commit), probe={"x": 1})) + "\n", "nested_value:probe"),
        (lambda commit: "\n" + json.dumps(_heartbeat(T0, 0, commit)) + "\n", "line_1:blank_record"),
        (
            lambda commit: json.dumps(_heartbeat(T0, 5, commit)) + "\n"
            + json.dumps(_heartbeat(T0 + dt.timedelta(hours=1), 5, commit)) + "\n",
            "seq_not_increasing",
        ),
        (
            lambda commit: json.dumps(_heartbeat(T0 + dt.timedelta(hours=1), 0, commit)) + "\n"
            + json.dumps(_heartbeat(T0, 1, commit)) + "\n",
            "timestamp_not_increasing",
        ),
        (lambda commit: json.dumps(_heartbeat(T0, 0, "b" * 40)) + "\n", "source_commit_mismatch"),
        (lambda commit: json.dumps(_heartbeat(T0, 0, commit, lock_digest="sha256:00")) + "\n", "lock_digest_mismatch"),
    ],
)
def test_bound_report_rejects_invalid_coverage_records(subject_repo, mutate, reason) -> None:
    repo, commit = subject_repo
    _write_lf(repo / COVERAGE_REL, mutate(commit))

    report = _bound(repo, commit)

    assert report["audit_result"] == "blocked"
    assert f"coverage_record_invalid:{COVERAGE_REL}" in report["blockers"]
    assert reason in report["coverage"][COVERAGE_REL]["invalid"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda commit: "[1, 2]\n",
        lambda commit: json.dumps(_heartbeat(T0, 0, commit)) + "\r"
        + json.dumps(_heartbeat(T0 + dt.timedelta(hours=1), 1, commit)) + "\n",
        lambda commit: json.dumps(_heartbeat(T0, 0, commit)) + "\n" + "{not json}\n",
    ],
)
def test_bound_report_rejects_appended_text_that_is_not_complete_jsonl(subject_repo, mutate) -> None:
    repo, commit = subject_repo
    _write_lf(repo / COVERAGE_REL, mutate(commit))

    report = _bound(repo, commit)

    assert report["audit_result"] == "blocked"
    assert f"raw_log_not_append_only:{COVERAGE_REL}" in report["blockers"]
    assert report["raw_log_binding"][COVERAGE_REL]["append_only"] is False


def test_crlf_folds_but_bare_cr_is_rejected_by_coverage_parsing(subject_repo) -> None:
    repo, commit = subject_repo
    _write_lf(repo / COVERAGE_REL, _stream(commit).replace("\n", "\r\n"))

    report = _bound(repo, commit)

    assert report["blockers"] == []
    assert report["coverage"][COVERAGE_REL]["records_healthy"] == 337
    instants, counts, reason = audit._coverage_instants(
        json.dumps(_heartbeat(T0, 0, commit)) + "\r", source_commit=commit, lock_digest=LOCK_DIGEST
    )
    assert (instants, counts["records_total"], reason) == ([], 0, "bare_cr")


def test_bound_report_nested_record_is_also_undated_for_the_legacy_scan(subject_repo) -> None:
    repo, commit = subject_repo
    _write_lf(repo / COVERAGE_REL, json.dumps(dict(_heartbeat(T0, 0, commit), probe={"status_http": 200})) + "\n")

    report = _bound(repo, commit)

    assert report["undated_record_count"] == 1
    assert "undated_records_detected" in report["blockers"]
    assert f"coverage_record_invalid:{COVERAGE_REL}" in report["blockers"]


def test_bound_report_probe_error_record_blocks_and_never_counts_as_coverage(subject_repo) -> None:
    repo, commit = subject_repo
    lines = _stream(commit).splitlines()
    failure = _heartbeat(
        T0 + dt.timedelta(hours=336, minutes=5), 400, commit,
        kind="soak_probe_error", state="probe_unavailable", error_count=1,
        probe_status_http=0, probe_latency_ms=10000, queries_ok=11,
    )
    _write_lf(repo / COVERAGE_REL, "\n".join([*lines, json.dumps(failure)]) + "\n")

    report = _bound(repo, commit, ended_at_utc=T1 + dt.timedelta(hours=1))

    assert report["audit_result"] == "blocked"
    assert "errors_detected" in report["blockers"]
    assert report["error_count"] == 1
    assert f"coverage_record_invalid:{COVERAGE_REL}" not in report["blockers"]
    stats = report["coverage"][COVERAGE_REL]
    assert stats["records_healthy"] == 337
    assert stats["records_nonhealthy"] == 1
    assert stats["records_in_window"] == 337


def test_bound_report_blocks_commit_mismatch_and_invalid_commit(subject_repo) -> None:
    repo, commit = subject_repo
    _write_lf(repo / COVERAGE_REL, _stream(commit))

    mismatch = _bound(repo, "b" * 40)
    invalid = _bound(repo, "not-a-commit")

    assert "source_commit_not_head" in mismatch["blockers"]
    assert mismatch["source_commit"] == "b" * 40
    assert "source_commit_invalid" in invalid["blockers"]
    assert invalid["source_commit"] is None
    assert invalid["audit_result"] == "blocked"


def test_bound_report_blocks_unrelated_tracked_edit_and_reads_lock_from_subject(subject_repo) -> None:
    repo, commit = subject_repo
    _write_lf(repo / COVERAGE_REL, _stream(commit))
    _write_lf(repo / "requirements.lock.txt", LOCK_TEXT + "extra==1.0\n")

    report = _bound(repo, commit)

    assert "source_worktree_dirty" in report["blockers"]
    assert report["worktree"]["changed_tracked_paths"] == [COVERAGE_REL, "requirements.lock.txt"]
    assert report["lock_digest"] == LOCK_DIGEST


def test_bound_report_blocks_diagnostic_source_append(subject_repo) -> None:
    repo, commit = subject_repo
    _write_lf(repo / COVERAGE_REL, _stream(commit))
    with (repo / ERROR_LOG_REL).open("ab") as handle:
        handle.write((json.dumps({"ts": "2026-09-16T00:00:00Z", "summary": "late note"}) + "\n").encode())

    report = _bound(repo, commit)

    assert f"diagnostic_source_modified:{ERROR_LOG_REL}" in report["blockers"]
    assert "source_worktree_dirty" in report["blockers"]
    assert report["raw_log_binding"][ERROR_LOG_REL]["append_only"] is False


def test_bound_report_blocks_rewritten_subject_prefix_and_incomplete_tail(subject_repo) -> None:
    repo, commit = subject_repo
    first = json.dumps(_heartbeat(T0, 0, commit)) + "\n"
    _write_lf(repo / COVERAGE_REL, first)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "subject S2 with one heartbeat")
    commit = _git(repo, "rev-parse", "HEAD")
    rewritten = json.dumps(_heartbeat(T0, 0, commit, queries_ok=11)) + "\n"

    _write_lf(repo / COVERAGE_REL, rewritten + _stream(commit))
    report_rewritten = _bound(repo, commit)
    _write_lf(repo / COVERAGE_REL, first + json.dumps(_heartbeat(T0 + dt.timedelta(hours=1), 1, commit)))
    report_tail = _bound(repo, commit)

    assert f"raw_log_not_append_only:{COVERAGE_REL}" in report_rewritten["blockers"]
    assert f"raw_log_not_append_only:{COVERAGE_REL}" in report_tail["blockers"]


def test_bound_report_window_and_generation_blockers(subject_repo) -> None:
    repo, commit = subject_repo
    _write_lf(repo / COVERAGE_REL, _stream(commit))

    missing = _bound(repo, commit, started_at_utc=None)
    short = _bound(repo, commit, ended_at_utc=T0 + dt.timedelta(hours=100), generated_at=T1)
    early = _bound(repo, commit, generated_at=T1 - dt.timedelta(minutes=1))
    inverted = _bound(repo, commit, ended_at_utc=T0)

    assert "window_explicit_required" in missing["blockers"]
    assert missing["window_hours"] is None
    assert "window_shorter_than_required" in short["blockers"]
    assert short["window_hours"] == 100.0
    assert "generated_before_end" in early["blockers"]
    assert "window_invalid" in inverted["blockers"]


def test_bound_report_blocks_coverage_path_missing_at_subject(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for key, value in (
        ("user.email", "soak@example.com"),
        ("user.name", "soak"),
        ("core.autocrlf", "false"),
        ("core.longpaths", "true"),
        ("commit.gpgsign", "false"),
    ):
        _git(repo, "config", key, value)
    _write_lf(repo / "requirements.lock.txt", LOCK_TEXT)
    _write_lf(repo / ERROR_LOG_REL, json.dumps(LEGACY_ERROR_LOG_RECORD) + "\n")
    _write_lf(repo / HISTORY_REL, json.dumps(HISTORY_ENVELOPE) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "subject without heartbeat path")
    commit = _git(repo, "rev-parse", "HEAD")
    _write_lf(repo / COVERAGE_REL, _stream(commit))

    report = _bound(repo, commit)

    assert f"raw_log_missing_at_subject:{COVERAGE_REL}" in report["blockers"]
    assert report["raw_log_binding"][COVERAGE_REL]["subject_blob"] is None
    assert report["audit_result"] == "blocked"


def test_bound_report_blocks_coverage_gap_and_empty_stream(subject_repo) -> None:
    repo, commit = subject_repo

    _write_lf(repo / COVERAGE_REL, _stream(commit, hole=(100, 126)))
    gap = _bound(repo, commit)
    _write_lf(repo / COVERAGE_REL, "")
    empty = _bound(repo, commit)

    assert f"coverage_gap_exceeded:{COVERAGE_REL}" in gap["blockers"]
    # Hours 100..125 are missing, so the gap runs from hour 99 to hour 126.
    assert gap["coverage"][COVERAGE_REL]["max_gap_seconds"] == 27 * 3600
    assert f"coverage_empty:{COVERAGE_REL}" in empty["blockers"]
    assert empty["coverage"][COVERAGE_REL]["records_in_window"] == 0
    assert empty["raw_log_binding"][COVERAGE_REL]["append_only"] is True


def test_bound_report_requires_exactly_one_listed_jsonl_coverage_source(subject_repo) -> None:
    repo, commit = subject_repo
    _write_lf(repo / COVERAGE_REL, _stream(commit))

    none = _bound(repo, commit, coverage_sources=[])
    two = _bound(repo, commit, coverage_sources=[Path(COVERAGE_REL), Path(ERROR_LOG_REL)])
    unlisted = _bound(repo, commit, coverage_sources=[Path("docs/runs/other.jsonl")])

    assert "coverage_source_missing" in none["blockers"]
    assert "coverage_source_count_invalid" in two["blockers"]
    assert "coverage_source_not_listed:docs/runs/other.jsonl" in unlisted["blockers"]
    assert "coverage_source_missing" in unlisted["blockers"]


def test_bound_report_rejects_absolute_and_traversal_source_paths(subject_repo) -> None:
    repo, commit = subject_repo
    _write_lf(repo / COVERAGE_REL, _stream(commit))

    report = _bound(
        repo,
        commit,
        sources=[Path(ERROR_LOG_REL), Path(HISTORY_REL), Path(COVERAGE_REL), Path("../outside.log"), repo / "abs.log"],
    )

    assert "source_path_invalid:../outside.log" in report["blockers"]
    assert any(item.startswith("source_path_invalid:") and item.endswith("abs.log") for item in report["blockers"])
    assert report["source_file_count"] == 3


def test_cli_bound_mode_writes_report_and_rejects_naive_window(subject_repo, tmp_path) -> None:
    # The CLI stamps generated_at from the wall clock, so the window must
    # already have ended: a 336 h window in the past relative to any run.
    repo, commit = subject_repo
    past_start = dt.datetime(2026, 8, 1, tzinfo=dt.UTC)
    lines = [
        json.dumps(_heartbeat(past_start + dt.timedelta(hours=hour), hour, commit))
        for hour in range(0, 337)
    ]
    _write_lf(repo / COVERAGE_REL, "\n".join(lines) + "\n")
    output = tmp_path / "audit.json"
    base = [
        "--output", str(output),
        "--source-root", str(repo),
        "--source-commit", commit,
        "--source", ERROR_LOG_REL,
        "--source", HISTORY_REL,
        "--source", COVERAGE_REL,
        "--coverage-source", COVERAGE_REL,
        "--started-at-utc", "2026-08-01T00:00:00Z",
    ]

    rc_ok = main([*base, "--ended-at-utc", "2026-08-15T00:00:00Z"])
    report_ok = json.loads(output.read_text(encoding="utf-8"))
    rc_naive = main([*base, "--ended-at-utc", "2026-08-15T00:00:00"])
    report_naive = json.loads(output.read_text(encoding="utf-8"))

    assert rc_ok == 0, report_ok["blockers"]
    assert report_ok["contract_version"] == CONTRACT_VERSION
    assert report_ok["audit_result"] == "pass"
    assert report_ok["generated_at"] >= report_ok["ended_at_utc"]
    assert report_ok["coverage"][COVERAGE_REL]["records_in_window"] == 337
    assert rc_naive == 1
    assert "window_invalid:ended_at_utc" in report_naive["blockers"]
    assert "window_explicit_required" in report_naive["blockers"]
