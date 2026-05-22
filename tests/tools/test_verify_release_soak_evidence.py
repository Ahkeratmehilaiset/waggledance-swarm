# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import json

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
