# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import json

import tools.verify_release_soak_evidence as verifier
from tools.collect_soak_evidence import build_soak_evidence
from tools.verify_release_soak_evidence import build_report, main


COMMIT = "dc76e81cd8c804608bfaedf951220e46ff1baffa"


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
