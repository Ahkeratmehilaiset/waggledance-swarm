# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess

import pytest

import tools.verify_release_soak_evidence as verifier
from tools.collect_soak_evidence import (
    AXIS_A_SOLVER_SCALE_PROOF,
    AXIS_B_HEX_ALIGNED_EVAL,
    FINAL_PIP_AUDIT_REPORTS,
    PRIVACY_PRECHECK,
    build_soak_evidence,
)
from pathlib import Path

from tools.release_axis_a_attestation import (
    AXIS_A_ALLOWED_FAMILIES,
    AXIS_A_DESCRIPTORS_PER_FAMILY,
    AXIS_A_DESCRIPTORS_PER_HEX_CELL,
    AXIS_A_EXPECTED_SOURCES,
    AXIS_A_HEX_CELLS,
    _source_digest as _helper_source_digest,
)
from tools.release_axis_b_attestation import (
    AXIS_B_CELLS,
    AXIS_B_EXPECTED_SOURCES,
)
from tools.release_security_attestation import (
    DEFAULT_REQUIREMENTS_LOCK,
    _lock_pin_multiset,
    evaluate_audited_lock_pins,
    evaluate_privacy_attestation,
)
from tools.verify_release_soak_evidence import build_report, main


COMMIT = "dc76e81cd8c804608bfaedf951220e46ff1baffa"

ATTESTATION_BLOCKERS = {
    "privacy_attestation_not_final",
    "privacy_attestation_missing_exact_line",
    "privacy_attestation_unreadable",
    "audited_lock_pins_stale",
    "audited_report_missing",
    "audited_report_unreadable",
    "requirements_lock_unreadable",
}

_FINAL_PRIVACY_TEXT = "# v3.12.0 final privacy receipt\n\n87 passed\nSMOKE_OK\n"


@pytest.mark.parametrize(
    "passed_line, accepted",
    [
        ("74 passed", True),
        ("87 passed", True),
        ("174 passed", True),
        ("73 passed", False),
        ("Result: 87 passed today", False),
        ("187 passed suffix", False),
        ("0 passed", False),
        ("eighty passed", False),
        ("1" * 5000 + " passed", False),
    ],
    ids=[
        "floor-74",
        "current-87",
        "higher-174",
        "under-floor-73",
        "embedded-sentence",
        "trailing-suffix",
        "zero",
        "non-integer",
        "hostile-very-long-count",
    ],
)
def test_privacy_passed_line_floor(tmp_path, passed_line, accepted) -> None:
    receipt = tmp_path / "receipt.md"
    receipt.write_text(
        f"# receipt\n\n{passed_line}\nSMOKE_OK\n", encoding="utf-8"
    )

    blockers = evaluate_privacy_attestation(receipt)

    if accepted:
        assert blockers == []
    else:
        assert blockers == ["privacy_attestation_missing_exact_line"]


def _real_lock_report_dependencies() -> list[dict[str, str]]:
    pins = _lock_pin_multiset(DEFAULT_REQUIREMENTS_LOCK)
    assert pins, "repo requirements.lock.txt must parse to pins"
    return [
        {"name": name, "version": version}
        for (name, version), count in sorted(pins.items())
        for _ in range(count)
    ]


def _attestation_env(
    tmp_path,
    *,
    privacy_text: str | None,
    report_dependencies: list[dict[str, str]] | None,
):
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    if privacy_text is not None:
        (evidence_root / PRIVACY_PRECHECK).write_text(
            privacy_text, encoding="utf-8"
        )
    if report_dependencies is not None:
        (evidence_root / FINAL_PIP_AUDIT_REPORTS[0]).write_text(
            json.dumps({"dependencies": report_dependencies}),
            encoding="utf-8",
        )
    return evidence_root


def _claiming_evidence(tmp_path, **fields) -> Path:
    evidence = {
        "commit": "abc123",
        "started_at_utc": "2026-05-10T00:00:00Z",
        "ended_at_utc": "2026-05-22T12:00:00Z",
        **fields,
    }
    soak_evidence = tmp_path / "v3.12.0.json"
    soak_evidence.write_text(json.dumps(evidence), encoding="utf-8")
    return soak_evidence


def _attestation_report(tmp_path, evidence_root, **evidence_fields):
    soak_evidence = _claiming_evidence(tmp_path, **evidence_fields)
    return build_report(
        soak_evidence=soak_evidence,
        release_readiness="docs/release/RELEASE_READINESS.md",
        evidence_root=evidence_root,
        release_notes=tmp_path / "v3.12.0.md",
    )


def test_attestation_blocks_non_final_privacy_receipt(tmp_path) -> None:
    evidence_root = _attestation_env(
        tmp_path,
        privacy_text=(
            "Status: preliminary local precheck only. "
            "This is not final stable evidence.\n\n74 passed\nSMOKE_OK\n"
        ),
        report_dependencies=_real_lock_report_dependencies(),
    )
    report = _attestation_report(
        tmp_path, evidence_root, security_privacy_gate="pass"
    )

    assert report["verified"] is False
    assert "privacy_attestation_not_final" in report["blockers"]
    assert str(tmp_path) not in json.dumps(report)


def test_attestation_blocks_embedded_privacy_tokens(tmp_path) -> None:
    evidence_root = _attestation_env(
        tmp_path,
        privacy_text="Result: 74 passed today\nSMOKE_OK_EXTRA marker\n",
        report_dependencies=_real_lock_report_dependencies(),
    )
    report = _attestation_report(
        tmp_path, evidence_root, profile_s_smoke="pass"
    )

    assert report["verified"] is False
    assert "privacy_attestation_missing_exact_line" in report["blockers"]
    assert "privacy_attestation_not_final" not in report["blockers"]


def _pin_expected_to_actual(monkeypatch):
    """Make the rebuilt expected evidence equal whatever actual claims."""

    def _mirror(release_readiness, *, commit, started_at_utc, ended_at_utc,
                use_local_artifacts, evidence_root, release_notes):
        return {
            "commit": commit,
            "started_at_utc": "2026-05-10T00:00:00Z",
            "ended_at_utc": "2026-05-22T12:00:00Z",
            "security_privacy_gate": "pass",
        }

    monkeypatch.setattr(verifier, "build_soak_evidence", _mirror)


def test_attestation_clears_final_exact_lines_and_matching_pins(
    tmp_path, monkeypatch
) -> None:
    evidence_root = _attestation_env(
        tmp_path,
        privacy_text=_FINAL_PRIVACY_TEXT,
        report_dependencies=_real_lock_report_dependencies(),
    )

    # Positive path proven directly: both helper evaluations return no
    # blockers for the final exact-line receipt and lock-matching report.
    assert (
        evaluate_privacy_attestation(evidence_root / PRIVACY_PRECHECK) == []
    )
    assert (
        evaluate_audited_lock_pins(
            evidence_root / FINAL_PIP_AUDIT_REPORTS[0]
        )
        == []
    )

    # And end to end: with actual equal to rebuilt expected (mirrored via
    # the established build_soak_evidence monkeypatch pattern) and a pass
    # claim active, the whole report verifies with no blockers at all.
    _pin_expected_to_actual(monkeypatch)
    report = _attestation_report(
        tmp_path,
        evidence_root,
        commit="abc123",
        security_privacy_gate="pass",
    )

    assert report["verified"] is True
    assert report["blockers"] == []


def test_attestation_blocks_only_on_non_final_when_fields_match(
    tmp_path, monkeypatch
) -> None:
    # Matching-fields counter-case: actual == expected so no ordinary
    # field mismatches exist, yet a non-final receipt alone must flip
    # verified to False through the attestation blocker.
    evidence_root = _attestation_env(
        tmp_path,
        privacy_text=(
            "This is not final stable evidence.\n\n74 passed\nSMOKE_OK\n"
        ),
        report_dependencies=_real_lock_report_dependencies(),
    )
    _pin_expected_to_actual(monkeypatch)
    report = _attestation_report(
        tmp_path,
        evidence_root,
        commit="abc123",
        security_privacy_gate="pass",
    )

    assert report["verified"] is False
    assert report["blockers"] == ["privacy_attestation_not_final"]
    assert report["mismatched_fields"] == []


@pytest.mark.parametrize(
    "marker_text",
    [
        "This is not final stable evidence.",
        "This is not\tfinal stable evidence.",
        "This is not  final stable evidence.",
        "This is not-final stable evidence.",
        "This is not​ final stable evidence.",
        "This is ｎｏｔ ｆｉｎａｌ stable evidence.",
        "This is not–final stable evidence.",
    ],
    ids=[
        "nbsp",
        "tab",
        "double-space",
        "hyphenated",
        "zero-width",
        "fullwidth",
        "en-dash",
    ],
)
def test_attestation_normalizes_unicode_non_final_markers(
    tmp_path, marker_text
) -> None:
    evidence_root = _attestation_env(
        tmp_path,
        privacy_text=marker_text + "\n\n74 passed\nSMOKE_OK\n",
        report_dependencies=_real_lock_report_dependencies(),
    )
    report = _attestation_report(
        tmp_path, evidence_root, security_privacy_gate="pass"
    )

    assert "privacy_attestation_not_final" in report["blockers"]


def test_attestation_blocks_drifted_lock_pins(tmp_path) -> None:
    dependencies = _real_lock_report_dependencies()
    dependencies[0] = {
        "name": dependencies[0]["name"],
        "version": dependencies[0]["version"] + ".drifted",
    }
    evidence_root = _attestation_env(
        tmp_path,
        privacy_text=_FINAL_PRIVACY_TEXT,
        report_dependencies=dependencies,
    )
    report = _attestation_report(
        tmp_path, evidence_root, security_privacy_gate="pass"
    )

    assert report["verified"] is False
    assert "audited_lock_pins_stale" in report["blockers"]


def test_attestation_missing_artifacts_fail_closed(tmp_path) -> None:
    evidence_root = _attestation_env(
        tmp_path, privacy_text=None, report_dependencies=None
    )
    report = _attestation_report(
        tmp_path, evidence_root, security_privacy_gate="pass"
    )

    assert report["verified"] is False
    assert "privacy_attestation_unreadable" in report["blockers"]
    assert "audited_report_missing" in report["blockers"]


def test_attestation_inactive_without_pass_claim(tmp_path) -> None:
    evidence_root = _attestation_env(
        tmp_path,
        privacy_text=(
            "Status: preliminary. This is not final stable evidence.\n"
        ),
        report_dependencies=[{"name": "aiohttp", "version": "0.0.1"}],
    )
    report = _attestation_report(tmp_path, evidence_root, result="hold")

    assert not (set(report["blockers"]) & ATTESTATION_BLOCKERS)


def _write_evidence(path, evidence_root, release_notes) -> dict:
    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit=COMMIT,
        ended_at_utc=dt.datetime(2026, 5, 22, 12, 0, tzinfo=dt.UTC),
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )
    path.write_text(json.dumps(evidence), encoding="utf-8")
    return evidence


def test_verifier_passes_reproducible_fail_closed_evidence(tmp_path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    soak_evidence = tmp_path / "v3.12.0.json"
    _write_evidence(soak_evidence, evidence_root, release_notes)

    report = build_report(
        soak_evidence=soak_evidence,
        release_readiness="docs/release/RELEASE_READINESS.md",
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert report["verified"] is True
    assert report["blockers"] == []
    assert report["mismatched_fields"] == []
    assert report["soak_evidence"] == "<redacted>"
    assert report["release_readiness"] == "<redacted>"
    assert report["evidence_root"] == "<redacted>"
    assert report["release_notes"] == "<redacted>"
    assert str(tmp_path) not in json.dumps(report)


def test_verifier_blocks_manual_status_stub(tmp_path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    soak_evidence = tmp_path / "v3.12.0.json"
    evidence = _write_evidence(soak_evidence, evidence_root, release_notes)
    evidence["ci_status"] = "pass"
    soak_evidence.write_text(json.dumps(evidence), encoding="utf-8")

    report = build_report(
        soak_evidence=soak_evidence,
        release_readiness="docs/release/RELEASE_READINESS.md",
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert report["verified"] is False
    assert "ci_status" in report["mismatched_fields"]
    assert "field_mismatch:ci_status" in report["blockers"]


def test_verifier_blocks_missing_nullable_field(tmp_path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    soak_evidence = tmp_path / "v3.12.0.json"
    evidence = _write_evidence(soak_evidence, evidence_root, release_notes)
    assert evidence["silent_failures"] is None
    del evidence["silent_failures"]
    soak_evidence.write_text(json.dumps(evidence), encoding="utf-8")

    report = build_report(
        soak_evidence=soak_evidence,
        release_readiness="docs/release/RELEASE_READINESS.md",
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert report["verified"] is False
    assert "silent_failures" in report["mismatched_fields"]
    assert "field_mismatch:silent_failures" in report["blockers"]


def test_verifier_blocks_extra_null_field(tmp_path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    soak_evidence = tmp_path / "v3.12.0.json"
    evidence = _write_evidence(soak_evidence, evidence_root, release_notes)
    evidence["unexpected_null"] = None
    soak_evidence.write_text(json.dumps(evidence), encoding="utf-8")

    report = build_report(
        soak_evidence=soak_evidence,
        release_readiness="docs/release/RELEASE_READINESS.md",
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert report["verified"] is False
    assert "unexpected_null" in report["mismatched_fields"]
    assert "field_mismatch:unexpected_null" in report["blockers"]


def test_verifier_blocks_manual_release_security_gate_flips(tmp_path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    soak_evidence = tmp_path / "v3.12.0.json"
    evidence = _write_evidence(soak_evidence, evidence_root, release_notes)
    evidence["security_privacy_gate"] = "pass"
    evidence["result"] = "pass"
    soak_evidence.write_text(json.dumps(evidence), encoding="utf-8")

    report = build_report(
        soak_evidence=soak_evidence,
        release_readiness="docs/release/RELEASE_READINESS.md",
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert report["verified"] is False
    assert "field_mismatch:security_privacy_gate" in report["blockers"]
    assert "field_mismatch:result" in report["blockers"]


def test_verifier_forwards_evidence_commit_and_window(monkeypatch, tmp_path) -> None:
    evidence_root = tmp_path / "evidence"
    release_notes = tmp_path / "v3.12.0.md"
    soak_evidence = tmp_path / "v3.12.0.json"
    actual = {
        "schema_version": "waggledance.release_soak_evidence.v1",
        "target_version": "v3.12.0",
        "commit": "abc123",
        "started_at_utc": "2026-05-10T00:00:00Z",
        "ended_at_utc": "2026-05-22T12:34:56Z",
        "result": "hold",
    }
    soak_evidence.write_text(json.dumps(actual), encoding="utf-8")
    captured = {}

    def fake_build_soak_evidence(
        release_readiness,
        *,
        commit,
        started_at_utc,
        ended_at_utc,
        use_local_artifacts,
        evidence_root,
        release_notes,
    ):
        captured.update(
            {
                "release_readiness": release_readiness,
                "commit": commit,
                "started_at_utc": started_at_utc,
                "ended_at_utc": ended_at_utc,
                "use_local_artifacts": use_local_artifacts,
                "evidence_root": evidence_root,
                "release_notes": release_notes,
            }
        )
        return dict(actual)

    monkeypatch.setattr(verifier, "build_soak_evidence", fake_build_soak_evidence)

    report = verifier.build_report(
        soak_evidence=soak_evidence,
        release_readiness="docs/release/RELEASE_READINESS.md",
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert report["verified"] is True
    assert captured["commit"] == "abc123"
    assert captured["started_at_utc"] == dt.datetime(2026, 5, 10, tzinfo=dt.UTC)
    assert captured["ended_at_utc"] == dt.datetime(
        2026, 5, 22, 12, 34, 56, tzinfo=dt.UTC
    )
    assert captured["use_local_artifacts"] is True
    assert captured["evidence_root"] == evidence_root
    assert captured["release_notes"] == release_notes


def test_verifier_cli_writes_report(tmp_path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    soak_evidence = tmp_path / "v3.12.0.json"
    output = tmp_path / "verify.json"
    _write_evidence(soak_evidence, evidence_root, release_notes)

    rc = main([
        "--soak-evidence",
        str(soak_evidence),
        "--release-readiness",
        "docs/release/RELEASE_READINESS.md",
        "--evidence-root",
        str(evidence_root),
        "--release-notes",
        str(release_notes),
        "--output",
        str(output),
    ])

    report = json.loads(output.read_text(encoding="utf-8"))
    assert rc == 0
    assert report["verified"] is True
    assert str(tmp_path) not in json.dumps(report)


# ---------------------------------------------------------------------------
# Axis V2 (2026-09-02): attestation helper wiring + source-subject binding
# ---------------------------------------------------------------------------

AXIS_SOURCES = tuple(
    dict.fromkeys(AXIS_A_EXPECTED_SOURCES + AXIS_B_EXPECTED_SOURCES)
)
NOT_IN_REPO = "0123456789abcdef0123456789abcdef01234567"
AXIS_A_ARTIFACT = Path(AXIS_A_SOLVER_SCALE_PROOF)
AXIS_B_ARTIFACT = Path(AXIS_B_HEX_ALIGNED_EVAL)


def _git(root, *args, check=True):
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=axis-v2-test",
            "-c",
            "user.email=axis-v2-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.longpaths=true",
            "-C",
            str(root),
            *args,
        ],
        check=check,
        capture_output=True,
        text=True,
    )


def _write_source(root, rel, text="value = 1\n"):
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(f"# {rel}\n{text}".encode("utf-8"))
    return target


def _init_source_repo(root, sources=AXIS_SOURCES):
    """A throwaway git repository holding the exact Axis inventories."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "core.autocrlf", "false")
    for rel in sources:
        _write_source(root, rel)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "axis sources")
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _commit_all(root, message="more"):
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _symlink_supported(tmp_path) -> bool:
    target = tmp_path / "symlink_probe_target"
    target.write_text("probe\n", encoding="utf-8")
    try:
        os.symlink(target, tmp_path / "symlink_probe_link")
    except (OSError, NotImplementedError):
        return False
    return True


def _axis_a_proof(root, commit):
    """The real shipped Axis A proof shape plus the V2 source binding."""
    return {
        "allowed_families": list(AXIS_A_ALLOWED_FAMILIES),
        "branch_name": "phase17a/producer-fabric-scale",
        "build_descriptors_per_second": 4960.9,
        "build_index_time_seconds": 2.0158,
        "builder_jobs_delta": 0,
        "descriptors_per_family": dict(AXIS_A_DESCRIPTORS_PER_FAMILY),
        "descriptors_per_hex_cell": dict(AXIS_A_DESCRIPTORS_PER_HEX_CELL),
        "families_total": 6,
        "finished_at_utc": "2026-05-22T12:20:45Z",
        "hex_cells": list(AXIS_A_HEX_CELLS),
        "hex_cells_total": 8,
        "hot_path_cache_stats": {
            "artifact_cache_size_after_lookup": 1000,
            "buffered_flushed_total": 0,
            "buffered_pending_signals": 0,
            "cold_hits_warmed": 1000,
            "misses": 0,
            "warm_hits": 1000,
            "warm_index_size_after_lookup": 1000,
        },
        "is_synthetic_scale": True,
        "lookup_benchmark_shape": "hot_path_cache_attached_warm_pass",
        "lookup_by_source": {"auto_promoted_solver": 1000},
        "lookup_capability_hits_total": 1000,
        "lookup_cold_after_attach": {
            "by_source": {"auto_promoted_solver": 1000},
            "lookup_capability_hits_total": 1000,
            "lookup_fifo_fallback_total": 0,
            "lookup_mean_ms": 2.4893,
            "lookup_miss_total": 0,
            "lookup_p50_ms": 1.9783,
            "lookup_p95_ms": 4.3457,
            "lookup_p99_ms": 6.633,
        },
        "lookup_fifo_fallback_total": 0,
        "lookup_mean_ms": 0.0143,
        "lookup_miss_total": 0,
        "lookup_p50_ms": 0.0149,
        "lookup_p95_ms": 0.0167,
        "lookup_p99_ms": 0.0214,
        "lookup_pass_count": 1000,
        "no_allowlist_widening": True,
        "no_provider_credentials_required": True,
        "no_runtime_network_required": True,
        "not_canonical_corpus": True,
        "phase": "phase17a_solver_scale",
        "production_hot_path_cache_attached": True,
        "provider_jobs_delta": 0,
        "schema_version": 1,
        "started_at_utc": "2026-05-22T12:20:41Z",
        "synthetic_solver_descriptors_total": 10000,
        "source_commit": commit,
        "generated_at": "2026-09-02T12:00:00Z",
        "source_files": list(AXIS_A_EXPECTED_SOURCES),
        "source_hashes": {
            rel: _helper_source_digest(root / rel)
            for rel in AXIS_A_EXPECTED_SOURCES
        },
    }


def _axis_b_row(cell, pos_correct=12):
    return {
        "cell": cell,
        "file": f"{cell}.yaml",
        "pos_correct": pos_correct,
        "pos_total": 15,
        "pos_score": round(pos_correct / 15, 4),
        "neg_correct": 5,
        "neg_total": 5,
        "neg_score": 1.0,
        "file_score": round((pos_correct / 15 + 1.0) / 2, 4),
    }


def _axis_b_report(root, commit):
    """A coherent Axis B report (quality 0.9) plus the V2 source binding."""
    return {
        "schema_version": "waggledance.axis_b_hex_eval.v1",
        "target_version": "v3.12.0",
        "benchmark_id": "v3.12-axis-b-hex-aligned-eval",
        "result": "pass",
        "blockers": [],
        "quality": 0.9,
        "micro_pos": 84,
        "micro_pos_total": 105,
        "micro_neg": 35,
        "micro_neg_total": 35,
        "corpus": {
            "cells": list(AXIS_B_CELLS),
            "files": 7,
            "total_positive": 105,
            "total_negative": 35,
            "oracle_dir": "tests/oracle_hex",
        },
        "thresholds": {
            "quality_floor": 0.74,
            "mismatched_baseline_quality": 0.5,
            "minimum_baseline_delta": 0.2,
            "per_cell_quality_floor": 0.6,
        },
        "per_file": [_axis_b_row(cell) for cell in AXIS_B_CELLS],
        "source_commit": commit,
        "generated_at": "2026-09-02T12:00:00Z",
        "source_files": list(AXIS_B_EXPECTED_SOURCES),
        "source_hashes": {
            rel: _helper_source_digest(root / rel)
            for rel in AXIS_B_EXPECTED_SOURCES
        },
    }


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _axis_env(tmp_path, *, axis_a=True, axis_b=True):
    root = tmp_path / "repo"
    head = _init_source_repo(root)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    if axis_a:
        _write_json(evidence_root / AXIS_A_ARTIFACT, _axis_a_proof(root, head))
    if axis_b:
        _write_json(evidence_root / AXIS_B_ARTIFACT, _axis_b_report(root, head))
    return root, head, evidence_root


def _mirror_expected(monkeypatch, **fields):
    """Rebuilt expected evidence = the actual commit plus ``fields``."""

    def _mirror(release_readiness, *, commit, started_at_utc, ended_at_utc,
                use_local_artifacts, evidence_root, release_notes):
        return {
            "commit": commit,
            "started_at_utc": "2026-05-10T00:00:00Z",
            "ended_at_utc": "2026-05-22T12:00:00Z",
            **fields,
        }

    monkeypatch.setattr(verifier, "build_soak_evidence", _mirror)


def _axis_verify(tmp_path, evidence_root, source_root, **evidence_fields):
    soak_evidence = _claiming_evidence(tmp_path, **evidence_fields)
    return build_report(
        soak_evidence=soak_evidence,
        release_readiness="docs/release/RELEASE_READINESS.md",
        evidence_root=evidence_root,
        release_notes=tmp_path / "v3.12.0.md",
        source_root=source_root,
    )


# --- primitives ------------------------------------------------------------


def test_lf_digest_matches_attestation_helper_digest(tmp_path) -> None:
    samples = [
        b"a\r\nb\rc\n",
        b"\xef\xbb\xbfbom\r\n",
        "unicode äö –\n".encode("utf-8"),
        b"no trailing newline",
        b"",
    ]
    for index, data in enumerate(samples):
        target = tmp_path / f"sample_{index}.txt"
        target.write_bytes(data)
        assert verifier.lf_digest(data) == _helper_source_digest(target)
    assert verifier.lf_digest(b"\xff\xfe\x00") is None


def test_source_subject_preflight_clean_repo_is_empty(tmp_path) -> None:
    root = tmp_path / "repo"
    head = _init_source_repo(root)

    assert verifier.source_subject_preflight(root, head) == []
    assert verifier.head_commit(root) == head


@pytest.mark.parametrize(
    "stamp",
    ["HEAD", NOT_IN_REPO.upper(), NOT_IN_REPO[:39], NOT_IN_REPO + "0", "", None, 7],
    ids=["head-word", "uppercase", "short", "long", "empty", "none", "int"],
)
def test_source_subject_preflight_rejects_malformed_stamp(tmp_path, stamp) -> None:
    root = tmp_path / "repo"
    _init_source_repo(root)

    assert verifier.source_subject_preflight(root, stamp) == [
        "source_commit_invalid"
    ]


def test_source_subject_preflight_rejects_well_formed_stamp_not_head(tmp_path) -> None:
    root = tmp_path / "repo"
    _init_source_repo(root)

    assert verifier.source_subject_preflight(root, NOT_IN_REPO) == [
        "source_commit_not_head"
    ]


def test_source_subject_preflight_rejects_dirty_tracked_and_untracked(tmp_path) -> None:
    root = tmp_path / "repo"
    head = _init_source_repo(root)
    tracked = root / AXIS_A_EXPECTED_SOURCES[0]
    original = tracked.read_bytes()

    tracked.write_bytes(original + b"# dirty\n")
    assert verifier.source_subject_preflight(root, head) == ["worktree_dirty"]

    tracked.write_bytes(original)
    assert verifier.source_subject_preflight(root, head) == []

    (root / "untracked.txt").write_text("stray\n", encoding="utf-8")
    assert verifier.source_subject_preflight(root, head) == ["worktree_dirty"]


def test_source_subject_preflight_git_unavailable(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    head = _init_source_repo(root)
    monkeypatch.setattr(verifier, "GIT_EXECUTABLE", "git-axis-v2-definitely-missing")

    assert verifier.source_subject_preflight(root, head) == ["git_unavailable"]
    assert verifier.resolve_commit(root, head) == (None, "git_unavailable")


def test_source_subject_preflight_not_a_repository(tmp_path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert verifier.source_subject_preflight(plain, NOT_IN_REPO) == [
        "git_revision_unresolvable"
    ]


def test_git_environment_cannot_retarget_pinned_repo(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    head = _init_source_repo(root)
    other = tmp_path / "other"
    other_head = _init_source_repo(other, sources=("README.md",))
    assert other_head != head
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(other / ".git" / "objects"))

    assert verifier.head_commit(root) == head
    assert verifier.source_subject_preflight(root, head) == []
    binding = verifier.bind_source_inventory(root, head, AXIS_A_EXPECTED_SOURCES)
    assert binding.blockers == []
    assert set(binding.digests) == set(AXIS_A_EXPECTED_SOURCES)


def test_bind_source_inventory_digests_match_helper(tmp_path) -> None:
    root = tmp_path / "repo"
    head = _init_source_repo(root)

    binding = verifier.bind_source_inventory(root, head, AXIS_A_EXPECTED_SOURCES)

    assert binding.blockers == []
    assert binding.details == []
    assert binding.digests == {
        rel: _helper_source_digest(root / rel) for rel in AXIS_A_EXPECTED_SOURCES
    }


def test_bind_source_inventory_catches_tamper_hidden_from_status(tmp_path) -> None:
    """assume-unchanged blinds ``git status``; the blob check still catches it."""
    root = tmp_path / "repo"
    head = _init_source_repo(root)
    rel = AXIS_A_EXPECTED_SOURCES[0]
    _git(root, "update-index", "--assume-unchanged", rel)
    (root / rel).write_bytes(b"# tampered\nvalue = 2\n")

    assert verifier.source_subject_preflight(root, head) == []
    binding = verifier.bind_source_inventory(root, head, AXIS_A_EXPECTED_SOURCES)

    assert binding.digests == {}
    assert binding.blockers == ["source_worktree_blob_mismatch"]
    assert binding.details == [f"source_worktree_blob_mismatch: {rel}"]
    assert all(str(tmp_path) not in line for line in binding.details)


def test_bind_source_inventory_untracked_at_commit(tmp_path) -> None:
    root = tmp_path / "repo"
    first = _init_source_repo(root, sources=AXIS_SOURCES[1:])
    _write_source(root, AXIS_SOURCES[0])
    second = _commit_all(root)

    stale = verifier.bind_source_inventory(root, first, AXIS_A_EXPECTED_SOURCES)
    current = verifier.bind_source_inventory(root, second, AXIS_A_EXPECTED_SOURCES)

    assert stale.blockers == ["source_not_tracked_at_commit"]
    assert stale.digests == {}
    assert current.blockers == []


def test_tracked_blob_bytes_returns_committed_bytes_not_worktree(tmp_path) -> None:
    """The bytes come from the object store even when the worktree differs."""
    root = tmp_path / "repo"
    head = _init_source_repo(root)
    rel = AXIS_A_EXPECTED_SOURCES[0]
    committed = (root / rel).read_bytes()
    _git(root, "update-index", "--assume-unchanged", rel)
    (root / rel).write_bytes(b"# tampered\nvalue = 2\n")

    data, blocker = verifier.tracked_blob_bytes(root, head, rel)

    assert blocker is None
    assert data == committed
    assert data != (root / rel).read_bytes()
    assert verifier.tracked_blob_digest(root, head, rel) == (
        verifier.lf_digest(committed),
        None,
    )
    assert verifier.tracked_blob_bytes(root, head, "not/tracked.py") == (
        None,
        "source_not_tracked_at_commit",
    )
    assert verifier.tracked_blob_bytes(root, NOT_IN_REPO, rel) == (
        None,
        "git_ls_tree_failed",
    )


def test_bind_source_inventory_missing_and_directory_entries(tmp_path) -> None:
    root = tmp_path / "repo"
    head = _init_source_repo(root)
    rel = AXIS_A_EXPECTED_SOURCES[0]
    (root / rel).unlink()

    missing = verifier.bind_source_inventory(root, head, AXIS_A_EXPECTED_SOURCES)
    assert missing.blockers == ["source_missing"]

    (root / rel).mkdir()
    (root / rel / "inner.py").write_text("x = 1\n", encoding="utf-8")
    directory = verifier.bind_source_inventory(root, head, AXIS_A_EXPECTED_SOURCES)
    assert directory.blockers == ["source_not_regular"]


def test_bind_source_inventory_rejects_symlinked_source(tmp_path) -> None:
    if not _symlink_supported(tmp_path):
        pytest.skip("symlinks unavailable in this environment")
    root = tmp_path / "repo"
    head = _init_source_repo(root)
    rel = AXIS_A_EXPECTED_SOURCES[0]
    real = tmp_path / "elsewhere.py"
    real.write_bytes((root / rel).read_bytes())
    (root / rel).unlink()
    os.symlink(real, root / rel)

    binding = verifier.bind_source_inventory(root, head, AXIS_A_EXPECTED_SOURCES)

    assert binding.blockers == ["source_link_or_reparse"]


def test_bind_source_inventory_rejects_malformed_commit(tmp_path) -> None:
    root = tmp_path / "repo"
    _init_source_repo(root)

    binding = verifier.bind_source_inventory(root, "HEAD", AXIS_A_EXPECTED_SOURCES)

    assert binding.blockers == ["source_commit_invalid"]
    assert binding.digests == {}


def test_bind_source_subject_runs_preflight_first(tmp_path) -> None:
    root = tmp_path / "repo"
    head = _init_source_repo(root)

    stale = verifier.bind_source_subject(root, NOT_IN_REPO, AXIS_A_EXPECTED_SOURCES)
    assert stale.blockers == ["source_commit_not_head"]
    clean = verifier.bind_source_subject(root, head, AXIS_A_EXPECTED_SOURCES)
    assert clean.blockers == []
    assert set(clean.digests) == set(AXIS_A_EXPECTED_SOURCES)


# --- verifier wiring -------------------------------------------------------


def test_axis_claims_inactive_without_pass(tmp_path, monkeypatch) -> None:
    root, head, evidence_root = _axis_env(tmp_path, axis_a=False, axis_b=False)
    _mirror_expected(monkeypatch, axis_a_regression="hold", axis_b_gate="hold")

    report = _axis_verify(
        tmp_path, evidence_root, root,
        commit=head, axis_a_regression="hold", axis_b_gate="hold",
    )

    assert report["verified"] is True
    assert report["blockers"] == []


def test_axis_pass_claim_without_artifact_holds(tmp_path, monkeypatch) -> None:
    """Named HOLD: a pass claim whose canonical artifact is missing."""
    root, head, evidence_root = _axis_env(tmp_path, axis_a=False, axis_b=False)
    _mirror_expected(monkeypatch, axis_a_regression="pass", axis_b_gate="pass")

    report = _axis_verify(
        tmp_path, evidence_root, root,
        commit=head, axis_a_regression="pass", axis_b_gate="pass",
    )

    assert report["verified"] is False
    assert "axis_a_report_unreadable" in report["blockers"]
    assert "axis_b_report_unreadable" in report["blockers"]
    assert report["mismatched_fields"] == []


def test_rebuilt_only_axis_pass_still_invokes_helpers(tmp_path, monkeypatch) -> None:
    root, head, evidence_root = _axis_env(tmp_path, axis_a=False)
    _mirror_expected(monkeypatch, axis_a_regression="pass", axis_b_gate="pass")

    report = _axis_verify(
        tmp_path, evidence_root, root,
        commit=head, axis_a_regression="hold", axis_b_gate="pass",
    )

    assert report["verified"] is False
    assert "field_mismatch:axis_a_regression" in report["blockers"]
    assert "axis_a_report_unreadable" in report["blockers"]
    assert not any(item.startswith("axis_b_") for item in report["blockers"])


def test_actual_only_axis_pass_still_invokes_helpers(tmp_path, monkeypatch) -> None:
    root, head, evidence_root = _axis_env(tmp_path, axis_b=False)
    _mirror_expected(monkeypatch, axis_a_regression="pass", axis_b_gate="hold")

    report = _axis_verify(
        tmp_path, evidence_root, root,
        commit=head, axis_a_regression="pass", axis_b_gate="pass",
    )

    assert report["verified"] is False
    assert "field_mismatch:axis_b_gate" in report["blockers"]
    assert "axis_b_report_unreadable" in report["blockers"]
    assert not any(item.startswith("axis_a_") for item in report["blockers"])


def test_coherent_axis_artifacts_bound_to_commit_verify(tmp_path, monkeypatch) -> None:
    root, head, evidence_root = _axis_env(tmp_path)
    _mirror_expected(monkeypatch, axis_a_regression="pass", axis_b_gate="pass")

    report = _axis_verify(
        tmp_path, evidence_root, root,
        commit=head, axis_a_regression="pass", axis_b_gate="pass",
    )

    assert report["verified"] is True
    assert report["blockers"] == []
    assert report["source_root"] == "<redacted>"
    assert str(tmp_path) not in json.dumps(report)


def test_helper_exception_is_verifier_error_never_pass(tmp_path, monkeypatch) -> None:
    root, head, evidence_root = _axis_env(tmp_path)
    _mirror_expected(monkeypatch, axis_a_regression="pass", axis_b_gate="pass")

    def _boom(report_path, source_root, expected_commit):
        raise RuntimeError("helper crashed")

    monkeypatch.setattr(verifier, "evaluate_axis_a_attestation", _boom)
    monkeypatch.setattr(
        verifier, "evaluate_axis_b_attestation", lambda *args: None
    )

    report = _axis_verify(
        tmp_path, evidence_root, root,
        commit=head, axis_a_regression="pass", axis_b_gate="pass",
    )

    assert report["verified"] is False
    assert "axis_a_helper_error:RuntimeError" in report["blockers"]
    assert "axis_b_helper_error:InvalidResult" in report["blockers"]


def test_forged_hashes_over_tampered_worktree_block(tmp_path, monkeypatch) -> None:
    """The helper alone passes this forge; the S-blob recheck must not."""
    root, head, evidence_root = _axis_env(tmp_path)
    rel = AXIS_A_EXPECTED_SOURCES[0]
    _git(root, "update-index", "--assume-unchanged", rel)
    (root / rel).write_bytes(b"# tampered\nvalue = 2\n")
    _write_json(evidence_root / AXIS_A_ARTIFACT, _axis_a_proof(root, head))
    assert verifier.evaluate_axis_a_attestation(
        evidence_root / AXIS_A_ARTIFACT, root, head
    ) == []
    _mirror_expected(monkeypatch, axis_a_regression="pass", axis_b_gate="pass")

    report = _axis_verify(
        tmp_path, evidence_root, root,
        commit=head, axis_a_regression="pass", axis_b_gate="pass",
    )

    assert report["verified"] is False
    assert "axis_a_source_worktree_blob_mismatch" in report["blockers"]
    assert not any(item.startswith("axis_b_") for item in report["blockers"])


def test_forged_artifact_hashes_on_clean_worktree_block(tmp_path, monkeypatch) -> None:
    root, head, evidence_root = _axis_env(tmp_path)
    proof = _axis_a_proof(root, head)
    proof["source_hashes"][AXIS_A_EXPECTED_SOURCES[0]] = verifier.lf_digest(
        b"# forged\n"
    )
    _write_json(evidence_root / AXIS_A_ARTIFACT, proof)
    _mirror_expected(monkeypatch, axis_a_regression="pass", axis_b_gate="pass")

    report = _axis_verify(
        tmp_path, evidence_root, root,
        commit=head, axis_a_regression="pass", axis_b_gate="pass",
    )

    assert report["verified"] is False
    assert "axis_a_source_hash_mismatch" in report["blockers"]
    assert "axis_a_source_blob_mismatch" in report["blockers"]


def test_well_formed_envelope_commit_not_in_repo_blocks(tmp_path, monkeypatch) -> None:
    """A truthful-looking stamp that ROOT cannot resolve is a HOLD."""
    root, head, evidence_root = _axis_env(tmp_path)
    _write_json(evidence_root / AXIS_A_ARTIFACT, _axis_a_proof(root, NOT_IN_REPO))
    _write_json(evidence_root / AXIS_B_ARTIFACT, _axis_b_report(root, NOT_IN_REPO))
    _mirror_expected(monkeypatch, axis_a_regression="pass", axis_b_gate="pass")

    report = _axis_verify(
        tmp_path, evidence_root, root,
        commit=NOT_IN_REPO, axis_a_regression="pass", axis_b_gate="pass",
    )

    assert report["verified"] is False
    assert "axis_a_git_revision_unresolvable" in report["blockers"]
    assert "axis_b_git_revision_unresolvable" in report["blockers"]


def test_envelope_commit_mismatching_artifact_stamp_blocks(tmp_path, monkeypatch) -> None:
    root, head, evidence_root = _axis_env(tmp_path)
    _write_json(evidence_root / AXIS_A_ARTIFACT, _axis_a_proof(root, NOT_IN_REPO))
    _mirror_expected(monkeypatch, axis_a_regression="pass", axis_b_gate="pass")

    report = _axis_verify(
        tmp_path, evidence_root, root,
        commit=head, axis_a_regression="pass", axis_b_gate="pass",
    )

    assert report["verified"] is False
    assert "axis_a_source_commit_mismatch" in report["blockers"]
    assert not any(item.startswith("axis_b_") for item in report["blockers"])


def test_source_not_tracked_at_envelope_commit_blocks(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    first = _init_source_repo(root, sources=AXIS_SOURCES[1:])
    _write_source(root, AXIS_SOURCES[0])
    _commit_all(root)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    # Stamp the artifacts with the earlier commit S at which one Axis A
    # source did not exist yet; the worktree digests still match, so the
    # helper alone is satisfied.
    _write_json(evidence_root / AXIS_A_ARTIFACT, _axis_a_proof(root, first))
    _write_json(evidence_root / AXIS_B_ARTIFACT, _axis_b_report(root, first))
    assert verifier.evaluate_axis_a_attestation(
        evidence_root / AXIS_A_ARTIFACT, root, first
    ) == []
    _mirror_expected(monkeypatch, axis_a_regression="pass", axis_b_gate="pass")

    report = _axis_verify(
        tmp_path, evidence_root, root,
        commit=first, axis_a_regression="pass", axis_b_gate="pass",
    )

    assert report["verified"] is False
    assert "axis_a_source_not_tracked_at_commit" in report["blockers"]
    assert not any(item.startswith("axis_b_") for item in report["blockers"])


def test_inventory_drift_blocks(tmp_path, monkeypatch) -> None:
    root, head, evidence_root = _axis_env(tmp_path)
    proof = _axis_a_proof(root, head)
    dropped = proof["source_files"].pop()
    proof["source_hashes"].pop(dropped)
    _write_json(evidence_root / AXIS_A_ARTIFACT, proof)
    _mirror_expected(monkeypatch, axis_a_regression="pass", axis_b_gate="pass")

    report = _axis_verify(
        tmp_path, evidence_root, root,
        commit=head, axis_a_regression="pass", axis_b_gate="pass",
    )

    assert report["verified"] is False
    assert "axis_a_sources_unbound" in report["blockers"]
    assert "axis_a_source_blob_mismatch" in report["blockers"]


def test_axis_b_noncanonical_corpus_blocks(tmp_path, monkeypatch) -> None:
    root, head, evidence_root = _axis_env(tmp_path)
    planted = _axis_b_report(root, head)
    planted["corpus"]["oracle_dir"] = "noncanonical"
    _write_json(evidence_root / AXIS_B_ARTIFACT, planted)
    _mirror_expected(monkeypatch, axis_a_regression="pass", axis_b_gate="pass")

    report = _axis_verify(
        tmp_path, evidence_root, root,
        commit=head, axis_a_regression="pass", axis_b_gate="pass",
    )

    assert report["verified"] is False
    assert "axis_b_corpus_mismatch" in report["blockers"]

    blocked = _axis_b_report(root, head)
    blocked["result"] = "blocked"
    blocked["blockers"] = ["oracle_dir_noncanonical"]
    _write_json(evidence_root / AXIS_B_ARTIFACT, blocked)

    report = _axis_verify(
        tmp_path, evidence_root, root,
        commit=head, axis_a_regression="pass", axis_b_gate="pass",
    )

    assert report["verified"] is False
    assert "axis_b_not_pass" in report["blockers"]


def test_git_unavailable_blocks_axis_verification(tmp_path, monkeypatch) -> None:
    root, head, evidence_root = _axis_env(tmp_path)
    _mirror_expected(monkeypatch, axis_a_regression="pass", axis_b_gate="pass")
    monkeypatch.setattr(verifier, "GIT_EXECUTABLE", "git-axis-v2-definitely-missing")

    report = _axis_verify(
        tmp_path, evidence_root, root,
        commit=head, axis_a_regression="pass", axis_b_gate="pass",
    )

    assert report["verified"] is False
    assert "axis_a_git_unavailable" in report["blockers"]
    assert "axis_b_git_unavailable" in report["blockers"]


def test_git_environment_cannot_retarget_verifier(tmp_path, monkeypatch) -> None:
    root, head, evidence_root = _axis_env(tmp_path)
    other = tmp_path / "other"
    _init_source_repo(other, sources=("README.md",))
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    _mirror_expected(monkeypatch, axis_a_regression="pass", axis_b_gate="pass")

    report = _axis_verify(
        tmp_path, evidence_root, root,
        commit=head, axis_a_regression="pass", axis_b_gate="pass",
    )

    assert report["verified"] is True
    assert report["blockers"] == []


def test_verifier_cli_exposes_no_source_root_override(tmp_path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--source-root", str(tmp_path)])

    assert excinfo.value.code == 2
