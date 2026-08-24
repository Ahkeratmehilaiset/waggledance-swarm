# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import json
import os

import pytest

from tools.release_soak_log_attestation import (
    evaluate_soak_log_source_attestation,
)


COMMIT = "d204299440af5b1c2d3e4f5a6b7c8d9e0f1a2b3c"
START = dt.datetime(2026, 5, 10, tzinfo=dt.UTC)
END = dt.datetime(2026, 5, 24, tzinfo=dt.UTC)


def _iso(value: dt.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _lf_sha256(path) -> str:
    import hashlib

    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_daily_sources(tmp_path, hours_step: int = 12) -> list[str]:
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    jsonl = logs / "runtime.jsonl"
    lines = []
    instant = START
    while instant <= END:
        lines.append(json.dumps({"ts_utc": _iso(instant), "msg": "ok"}))
        instant += dt.timedelta(hours=hours_step)
    jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")

    text_log = logs / "runtime.log"
    text_lines = []
    instant = START + dt.timedelta(hours=6)
    while instant <= END:
        text_lines.append(f"{_iso(instant)} heartbeat ok")
        instant += dt.timedelta(hours=hours_step)
    text_log.write_text("\n".join(text_lines) + "\n", encoding="utf-8")
    return ["logs/runtime.jsonl", "logs/runtime.log"]


def _clean_report(tmp_path, files: list[str], **overrides) -> dict:
    report = {
        "schema_version": "waggledance.release_soak_log_audit.v1",
        "audit_result": "pass",
        "error_log_clean": True,
        "blockers": [],
        "silent_failure_count": 0,
        "error_count": 0,
        "undated_record_count": 0,
        "source_commit": COMMIT,
        "started_at_utc": _iso(START),
        "ended_at_utc": _iso(END),
        "generated_at": _iso(END + dt.timedelta(hours=1)),
        "source_files": list(files),
        "source_file_count": len(files),
        "source_hashes": {
            rel: _lf_sha256(tmp_path / rel) for rel in files
        },
    }
    report.update(overrides)
    return report


def _write_report(tmp_path, report) -> "os.PathLike":
    report_path = tmp_path / "soak_log_audit.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return report_path


def _evaluate(tmp_path, report):
    return evaluate_soak_log_source_attestation(
        _write_report(tmp_path, report), tmp_path, COMMIT
    )


def test_truthful_daily_mix_passes(tmp_path) -> None:
    files = _write_daily_sources(tmp_path)
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, files))

    assert blockers == []


def test_one_line_nominal_window_forgery_blocks_coverage(tmp_path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    one = logs / "one.jsonl"
    one.write_text(
        json.dumps({"ts_utc": _iso(START), "msg": "ok"}) + "\n",
        encoding="utf-8",
    )
    files = ["logs/one.jsonl"]
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, files))

    assert blockers == ["soak_log_coverage_insufficient"]


def test_canonical_shape_missing_commit_and_generated_block(tmp_path) -> None:
    files = _write_daily_sources(tmp_path)
    report = _clean_report(tmp_path, files)
    del report["source_commit"]
    del report["generated_at"]
    blockers = _evaluate(tmp_path, report)

    assert "soak_log_source_commit_missing" in blockers
    assert "soak_log_generated_at_invalid" in blockers


@pytest.mark.parametrize(
    "overrides",
    [
        {"audit_result": "blocked"},
        {"error_log_clean": "True"},
        {"error_log_clean": 1},
        {"blockers": ["errors_detected"]},
        {"silent_failure_count": False},
        {"error_count": "0"},
        {"undated_record_count": 1},
    ],
    ids=[
        "result-blocked",
        "clean-string",
        "clean-int-one",
        "blockers-nonempty",
        "count-bool",
        "count-string",
        "count-nonzero",
    ],
)
def test_dirty_or_nonliteral_clean_fields_block(tmp_path, overrides) -> None:
    files = _write_daily_sources(tmp_path)
    blockers = _evaluate(
        tmp_path, _clean_report(tmp_path, files, **overrides)
    )

    assert "soak_log_not_clean" in blockers


def test_mismatched_commit_blocks(tmp_path) -> None:
    files = _write_daily_sources(tmp_path)
    blockers = _evaluate(
        tmp_path, _clean_report(tmp_path, files, source_commit="a" * 40)
    )

    assert "soak_log_source_commit_mismatch" in blockers


@pytest.mark.parametrize(
    "bad_commit",
    ["", COMMIT.upper(), COMMIT[:-1], None],
    ids=["empty", "uppercase", "short", "none"],
)
def test_invalid_expected_commit_fails_closed(tmp_path, bad_commit) -> None:
    files = _write_daily_sources(tmp_path)
    report_path = _write_report(
        tmp_path, _clean_report(tmp_path, files)
    )

    blockers = evaluate_soak_log_source_attestation(
        report_path, tmp_path, bad_commit
    )

    assert blockers == ["expected_commit_invalid"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"started_at_utc": "2026-05-10T00:00:00"},
        {"ended_at_utc": "2026-05-24T03:00:00+03:00"},
        {"ended_at_utc": "2026-05-12T00:00:00Z"},
        {"started_at_utc": None},
    ],
    ids=["naive-start", "nonzero-offset-end", "short-window", "missing-start"],
)
def test_invalid_window_blocks(tmp_path, overrides) -> None:
    files = _write_daily_sources(tmp_path)
    blockers = _evaluate(
        tmp_path, _clean_report(tmp_path, files, **overrides)
    )

    assert "soak_log_window_invalid" in blockers


def test_generated_before_end_blocks(tmp_path) -> None:
    files = _write_daily_sources(tmp_path)
    blockers = _evaluate(
        tmp_path,
        _clean_report(
            tmp_path,
            files,
            generated_at=_iso(END - dt.timedelta(hours=1)),
        ),
    )

    assert "soak_log_generated_at_invalid" in blockers


_FAKE_DIGEST = "sha256:" + "0" * 64


def _set_consistent_sources(report, tmp_path, entries) -> None:
    """Make source fields internally consistent for ``entries``.

    Count always matches, and every entry gets a digest - the real
    LF-normalized digest where the file exists, a plausible fake
    otherwise - so each grid case blocks on its NAMED defect rather
    than on the count/keyset precheck.
    """
    report["source_files"] = list(entries)
    report["source_file_count"] = len(entries)
    hashes = {}
    for entry in entries:
        candidate = tmp_path / entry.replace("\\", "/")
        try:
            hashes[entry] = _lf_sha256(candidate)
        except (OSError, ValueError):
            hashes[entry] = _FAKE_DIGEST
    report["source_hashes"] = hashes


@pytest.mark.parametrize(
    "extra_entry",
    [
        "logs/runtime.jsonl",
        "logs\\runtime.jsonl",
        "LOGS/RUNTIME.JSONL",
        "C:/windows/system32/evil.log",
        "../outside.log",
        "logs/missing.log",
        "logs/readme.txt",
    ],
    ids=[
        "duplicate",
        "separator-alias",
        "casefold-alias",
        "absolute",
        "traversal",
        "missing-file",
        "bad-suffix",
    ],
)
def test_unbound_source_inventories_block(tmp_path, extra_entry) -> None:
    files = _write_daily_sources(tmp_path)
    report = _clean_report(tmp_path, files)
    _set_consistent_sources(report, tmp_path, files + [extra_entry])
    blockers = _evaluate(tmp_path, report)

    assert "soak_log_sources_unbound" in blockers


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report, files: report.update({"source_file_count": True}),
        lambda report, files: report["source_hashes"].pop(files[1]),
        lambda report, files: report.update(
            {"source_files": [], "source_file_count": 0, "source_hashes": {}}
        ),
        lambda report, files: report.update({"source_files": "logs"}),
    ],
    ids=["bool-count", "hash-keyset-drift", "empty-list", "non-list"],
)
def test_unbound_source_structures_block(tmp_path, mutate) -> None:
    files = _write_daily_sources(tmp_path)
    report = _clean_report(tmp_path, files)
    mutate(report, files)
    blockers = _evaluate(tmp_path, report)

    assert "soak_log_sources_unbound" in blockers


def test_parent_symlink_component_is_unbound(tmp_path) -> None:
    files = _write_daily_sources(tmp_path)
    linkdir = tmp_path / "linkdir"
    try:
        os.symlink(tmp_path / "logs", linkdir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")
    report = _clean_report(tmp_path, files)
    _set_consistent_sources(
        report, tmp_path, files + ["linkdir/runtime.log"]
    )
    blockers = _evaluate(tmp_path, report)

    assert "soak_log_sources_unbound" in blockers


def test_undated_log_line_blocks_coverage(tmp_path) -> None:
    files = _write_daily_sources(tmp_path)
    log_path = tmp_path / "logs" / "runtime.log"
    log_path.write_text(
        log_path.read_text(encoding="utf-8") + "orphan line without ts\n",
        encoding="utf-8",
    )
    report = _clean_report(tmp_path, files)
    blockers = _evaluate(tmp_path, report)

    assert "soak_log_coverage_insufficient" in blockers


def test_undated_jsonl_record_blocks_coverage(tmp_path) -> None:
    files = _write_daily_sources(tmp_path)
    jsonl_path = tmp_path / "logs" / "runtime.jsonl"
    jsonl_path.write_text(
        jsonl_path.read_text(encoding="utf-8")
        + json.dumps({"msg": "undated failure"})
        + "\n",
        encoding="utf-8",
    )
    report = _clean_report(tmp_path, files)
    blockers = _evaluate(tmp_path, report)

    assert "soak_log_coverage_insufficient" in blockers


def test_out_of_window_records_do_not_rescue_coverage(tmp_path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    sparse = logs / "sparse.jsonl"
    instants = [
        START - dt.timedelta(hours=48),
        START,
        END,
        END + dt.timedelta(hours=48),
    ]
    sparse.write_text(
        "\n".join(json.dumps({"ts_utc": _iso(i)}) for i in instants) + "\n",
        encoding="utf-8",
    )
    files = ["logs/sparse.jsonl"]
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, files))

    assert "soak_log_coverage_insufficient" in blockers


@pytest.mark.parametrize(
    "window_hours",
    [True, "336", 0, -5],
    ids=["bool", "string", "zero", "negative"],
)
def test_invalid_required_window_param_blocks(tmp_path, window_hours) -> None:
    files = _write_daily_sources(tmp_path)
    report_path = _write_report(tmp_path, _clean_report(tmp_path, files))

    blockers = evaluate_soak_log_source_attestation(
        report_path, tmp_path, COMMIT, required_window_hours=window_hours
    )

    assert "soak_log_window_invalid" in blockers


@pytest.mark.parametrize(
    "gap_hours",
    [True, "24", 0, -1, float("inf")],
    ids=["bool", "string", "zero", "negative", "infinite"],
)
def test_invalid_max_gap_param_blocks(tmp_path, gap_hours) -> None:
    files = _write_daily_sources(tmp_path)
    report_path = _write_report(tmp_path, _clean_report(tmp_path, files))

    blockers = evaluate_soak_log_source_attestation(
        report_path, tmp_path, COMMIT, max_gap_hours=gap_hours
    )

    assert "soak_log_coverage_insufficient" in blockers


def test_symlink_source_is_unbound(tmp_path) -> None:
    files = _write_daily_sources(tmp_path)
    link = tmp_path / "logs" / "alias.log"
    try:
        os.symlink(tmp_path / "logs" / "runtime.log", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")
    report = _clean_report(tmp_path, files)
    report["source_files"] = files + ["logs/alias.log"]
    report["source_file_count"] = len(report["source_files"])
    report["source_hashes"]["logs/alias.log"] = _lf_sha256(link)
    blockers = _evaluate(tmp_path, report)

    assert "soak_log_sources_unbound" in blockers


def test_hash_drift_blocks(tmp_path) -> None:
    files = _write_daily_sources(tmp_path)
    report = _clean_report(tmp_path, files)
    (tmp_path / files[0]).write_text(
        json.dumps({"ts_utc": _iso(START), "msg": "tampered"}) + "\n",
        encoding="utf-8",
    )
    blockers = _evaluate(tmp_path, report)

    assert "soak_log_source_hash_mismatch" in blockers


@pytest.mark.parametrize(
    "payload",
    ["{not json}\n", json.dumps({"ts_utc": "yesterday"}) + "\n"],
    ids=["malformed-jsonl", "malformed-record-timestamp"],
)
def test_malformed_records_block_coverage(tmp_path, payload) -> None:
    files = _write_daily_sources(tmp_path)
    extra = tmp_path / "logs" / "extra.jsonl"
    extra.write_text(payload, encoding="utf-8")
    all_files = files + ["logs/extra.jsonl"]
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, all_files))

    assert "soak_log_coverage_insufficient" in blockers


def test_endpoint_gap_boundary(tmp_path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    ok = logs / "ok.jsonl"
    lines = []
    instant = START + dt.timedelta(hours=24)
    while instant <= END:
        lines.append(json.dumps({"ts_utc": _iso(instant)}))
        instant += dt.timedelta(hours=12)
    ok.write_text("\n".join(lines) + "\n", encoding="utf-8")
    files = ["logs/ok.jsonl"]

    # First record exactly max_gap after start: allowed.
    assert _evaluate(tmp_path, _clean_report(tmp_path, files)) == []

    late = logs / "late.jsonl"
    late_lines = []
    instant = START + dt.timedelta(hours=25)
    while instant <= END:
        late_lines.append(json.dumps({"ts_utc": _iso(instant)}))
        instant += dt.timedelta(hours=12)
    late.write_text("\n".join(late_lines) + "\n", encoding="utf-8")
    late_files = ["logs/late.jsonl"]

    blockers = _evaluate(tmp_path, _clean_report(tmp_path, late_files))
    assert "soak_log_coverage_insufficient" in blockers


def test_interior_gap_boundary(tmp_path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    gap = logs / "gap.jsonl"
    instants = []
    instant = START
    while instant <= START + dt.timedelta(hours=96):
        instants.append(instant)
        instant += dt.timedelta(hours=12)
    resume = START + dt.timedelta(hours=96 + 25)
    while resume <= END:
        instants.append(resume)
        resume += dt.timedelta(hours=12)
    gap.write_text(
        "\n".join(json.dumps({"ts_utc": _iso(i)}) for i in instants) + "\n",
        encoding="utf-8",
    )
    files = ["logs/gap.jsonl"]

    blockers = _evaluate(tmp_path, _clean_report(tmp_path, files))
    assert "soak_log_coverage_insufficient" in blockers


def test_hostile_nested_types_never_crash_or_leak(tmp_path) -> None:
    report = {
        "audit_result": ["pass"],
        "error_log_clean": {"nested": True},
        "blockers": "none",
        "silent_failure_count": [0],
        "error_count": None,
        "undated_record_count": "zero",
        "source_commit": {"sha": COMMIT},
        "started_at_utc": 123,
        "ended_at_utc": ["2026-05-24"],
        "generated_at": {"at": "now"},
        "source_files": {"a": 1},
        "source_file_count": "2",
        "source_hashes": ["sha256:x"],
    }
    blockers = _evaluate(tmp_path, report)

    assert "soak_log_not_clean" in blockers
    assert "soak_log_source_commit_mismatch" in blockers
    assert "soak_log_window_invalid" in blockers
    assert "soak_log_generated_at_invalid" in blockers
    assert "soak_log_sources_unbound" in blockers
    encoded = json.dumps(blockers)
    assert str(tmp_path) not in encoded
    assert "system32" not in encoded


def test_unreadable_and_non_object_fail_closed(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    assert evaluate_soak_log_source_attestation(
        missing, tmp_path, COMMIT
    ) == ["soak_log_report_unreadable"]

    not_object = tmp_path / "list.json"
    not_object.write_text("[1]", encoding="utf-8")
    assert evaluate_soak_log_source_attestation(
        not_object, tmp_path, COMMIT
    ) == ["soak_log_report_unreadable"]
