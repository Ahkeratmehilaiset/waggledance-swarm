# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import json

import pytest

import tools.verify_release_soak_evidence as verifier
from tools.collect_soak_evidence import (
    FINAL_PIP_AUDIT_REPORTS,
    PRIVACY_PRECHECK,
    build_soak_evidence,
)
from pathlib import Path

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
