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


FRESH_COVERAGE = "docs/runs/release_soak_evidence/v3.12.0_soak_heartbeat.jsonl"
FRESH_DIAGNOSTICS = [
    "docs/runs/error_log.jsonl",
    "docs/runs/release_soak_evidence/v3.12.0_history.jsonl",
]


def _fresh_report(tmp_path):
    # Synthetic unit-test coverage, never a receipt of actual runtime hours.
    import shutil
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for rel in FRESH_DIAGNOSTICS:
        destination = tmp_path / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / rel, destination)
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("example==1.0\n", encoding="utf-8")
    start = dt.datetime(2026, 9, 11, tzinfo=dt.UTC)
    end = start + dt.timedelta(hours=336)
    records = [
        {
            "ts_utc": _iso(start + dt.timedelta(hours=hour)),
            "kind": "soak_heartbeat", "state": "ok", "source_commit": COMMIT,
            "seq": index, "lock_digest": _lf_sha256(lock),
        }
        for index, hour in enumerate(range(0, 337, 12))
    ]
    (tmp_path / FRESH_COVERAGE).write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    files = [*FRESH_DIAGNOSTICS, FRESH_COVERAGE]
    return _clean_report(
        tmp_path, files,
        contract_version="waggledance.release_soak_log_audit_fields.v2",
        target_version="v3.12.0",
        source_tree="a" * 40,
        source_roles={**dict.fromkeys(FRESH_DIAGNOSTICS, "diagnostic"),
                      FRESH_COVERAGE: "coverage"},
        coverage_sources=[FRESH_COVERAGE],
        lock_path="requirements.lock.txt", lock_digest=_lf_sha256(lock),
        started_at_utc=_iso(start), ended_at_utc=_iso(end),
        generated_at=_iso(end + dt.timedelta(minutes=1)),
    )


def test_fresh_contract_preserves_real_historical_diagnostic_records(tmp_path):
    report = _fresh_report(tmp_path)
    assert _evaluate(tmp_path, report) == []


@pytest.mark.parametrize("change", [
    {"contract_version": "unknown"},
    {"coverage_sources": FRESH_DIAGNOSTICS},
    {"source_roles": {}},
    {"lock_path": "different.lock"},
    {"lock_digest": "sha256:" + "0" * 64},
    {"source_tree": "not-a-tree"},
])
def test_fresh_contract_rejects_inconsistent_metadata(tmp_path, change):
    report = _fresh_report(tmp_path)
    report.update(change)
    assert _evaluate(tmp_path, report)


@pytest.mark.parametrize("change", [
    {"source_commit": "b" * 40},
    {"lock_digest": "sha256:" + "0" * 64},
    {"state": "degraded"},
    {"seq": True},
    {"seq": 0},
    {"created_at": "2026-09-11T00:00:00Z"},
    {"error_count": -1},
    {"error_count": False},
    {"app_errors": 1},
    {"connection_errors": "0"},
    {"probe": {"status_http": 200}},
])
def test_fresh_coverage_requires_consistent_typed_records(tmp_path, change):
    report = _fresh_report(tmp_path)
    coverage = tmp_path / FRESH_COVERAGE
    records = [json.loads(line) for line in coverage.read_text().splitlines()]
    records[1].update(change)
    coverage.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    report["source_hashes"][FRESH_COVERAGE] = _lf_sha256(coverage)
    assert "soak_log_coverage_insufficient" in _evaluate(tmp_path, report)


def test_fresh_diagnostic_counts_are_recomputed(tmp_path):
    report = _fresh_report(tmp_path)
    diagnostic = tmp_path / FRESH_DIAGNOSTICS[0]
    with diagnostic.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"ts_utc": "2026-09-12T00:00:00Z", "error_count": 1}) + "\n")
    report["source_hashes"][FRESH_DIAGNOSTICS[0]] = _lf_sha256(diagnostic)
    assert "soak_log_source_counts_mismatch" in _evaluate(tmp_path, report)


def test_fresh_coverage_requires_complete_jsonl_record(tmp_path):
    report = _fresh_report(tmp_path)
    coverage = tmp_path / FRESH_COVERAGE
    coverage.write_bytes(coverage.read_bytes().rstrip(b"\r\n"))
    report["source_hashes"][FRESH_COVERAGE] = _lf_sha256(coverage)
    assert "soak_log_coverage_insufficient" in _evaluate(tmp_path, report)


@pytest.mark.parametrize("separator", ["\u2028", "\u2029"])
def test_fresh_coverage_keeps_unicode_separator_inside_json_string(tmp_path, separator):
    report = _fresh_report(tmp_path)
    coverage = tmp_path / FRESH_COVERAGE
    records = [json.loads(line) for line in coverage.read_text().splitlines()]
    records[1]["note"] = "hello" + separator + "world"
    coverage.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    report["source_hashes"][FRESH_COVERAGE] = _lf_sha256(coverage)
    assert _evaluate(tmp_path, report) == []


def test_required_fresh_contract_does_not_fall_back_to_legacy(tmp_path):
    report = _clean_report(tmp_path, _write_daily_sources(tmp_path))
    blockers = evaluate_soak_log_source_attestation(
        _write_report(tmp_path, report), tmp_path, COMMIT, require_fresh_contract=True,
    )
    assert "soak_log_fresh_contract_required" in blockers


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
        {"schema_version": "waggledance.release_soak_log_audit.v2"},
    ],
    ids=[
        "result-blocked",
        "clean-string",
        "clean-int-one",
        "blockers-nonempty",
        "count-bool",
        "count-string",
        "count-nonzero",
        "schema-drift",
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
        "logs/./runtime.log",
    ],
    ids=[
        "duplicate",
        "separator-alias",
        "casefold-alias",
        "absolute",
        "traversal",
        "missing-file",
        "bad-suffix",
        "dot-alias",
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


def test_symlinked_source_root_is_unbound(tmp_path) -> None:
    files = _write_daily_sources(tmp_path)
    root_link = tmp_path.parent / (tmp_path.name + "-rootlink")
    try:
        os.symlink(tmp_path, root_link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")
    report_path = _write_report(tmp_path, _clean_report(tmp_path, files))

    blockers = evaluate_soak_log_source_attestation(
        report_path, root_link, COMMIT
    )

    assert "soak_log_sources_unbound" in blockers


def test_hardlink_same_file_alias_is_unbound(tmp_path) -> None:
    files = _write_daily_sources(tmp_path)
    link = tmp_path / "logs" / "hardlink.log"
    try:
        os.link(tmp_path / "logs" / "runtime.log", link)
    except (OSError, NotImplementedError):
        pytest.skip("hardlink creation not permitted on this host")
    report = _clean_report(tmp_path, files)
    _set_consistent_sources(
        report, tmp_path, files + ["logs/hardlink.log"]
    )
    blockers = _evaluate(tmp_path, report)

    assert "soak_log_sources_unbound" in blockers


def test_deep_nested_report_is_unreadable(tmp_path) -> None:
    report_path = tmp_path / "deep.json"
    depth = 200_000
    report_path.write_text("[" * depth + "]" * depth, encoding="utf-8")

    blockers = evaluate_soak_log_source_attestation(
        report_path, tmp_path, COMMIT
    )

    assert blockers == ["soak_log_report_unreadable"]


def test_deep_nested_source_json_blocks_coverage(tmp_path) -> None:
    files = _write_daily_sources(tmp_path)
    deep = tmp_path / "logs" / "deep.json"
    depth = 200_000
    deep.write_text("[" * depth + "]" * depth, encoding="utf-8")
    all_files = files + ["logs/deep.json"]
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, all_files))

    assert "soak_log_coverage_insufficient" in blockers


def test_one_record_many_keys_cannot_forge_coverage(tmp_path) -> None:
    # CRITICAL regression: a single record carrying every recognized
    # timestamp key at 24h intervals across the window must NOT count
    # as continuous coverage - a record contributes at most one instant.
    from tools.release_soak_log_attestation import TIMESTAMP_KEYS

    logs = tmp_path / "logs"
    logs.mkdir()
    forged = logs / "forged.jsonl"
    record = {
        key: _iso(START + dt.timedelta(hours=12 + 24 * index))
        for index, key in enumerate(TIMESTAMP_KEYS)
    }
    forged.write_text(json.dumps(record) + "\n", encoding="utf-8")
    files = ["logs/forged.jsonl"]
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, files))

    assert "soak_log_coverage_insufficient" in blockers


def test_multi_key_record_is_ambiguous_and_blocks(tmp_path) -> None:
    # Even two VALID recognized keys on one record are ambiguous
    # (summary started/ended metadata is not a runtime heartbeat).
    files = _write_daily_sources(tmp_path)
    extra = tmp_path / "logs" / "twokey.jsonl"
    extra.write_text(
        json.dumps(
            {
                "ts_utc": _iso(START),
                "ended_at_utc": _iso(START + dt.timedelta(hours=1)),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    all_files = files + ["logs/twokey.jsonl"]
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, all_files))

    assert "soak_log_coverage_insufficient" in blockers


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
    [True, "336", 0, -5, 10**1000],
    ids=["bool", "string", "zero", "negative", "huge-int"],
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
    [True, "24", 0, -1, float("inf"), 10**1000],
    ids=["bool", "string", "zero", "negative", "infinite", "huge-int"],
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
