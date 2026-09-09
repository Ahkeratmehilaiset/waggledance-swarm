# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import json
import os

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
    "audited_report_selection_blocked",
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
# Explicit-selection round-trip (lead expanded_changes_requested
# 2026-08-26T07:27:33Z; tools finding: evidence built with an explicit
# --bandit-report/--pip-audit-report must verify, with recovery restricted
# to validated root-relative recorded paths).

_RT_PIP_OLD = "v3.12.0_pip_audit_report_lock_after_prune_osv.json"
_RT_PIP_NEW = "v3.12.0_pip_audit_report.json"
_RT_BANDIT_ZM = "v3.12.0_bandit_report_after_static_hardening_zero_medium.json"
_RT_BANDIT_PLAIN = "v3.12.0_bandit_report.json"


def _rt_release_notes(tmp_path) -> Path:
    release_notes = tmp_path / "v3.12.0.md"
    release_notes.write_text(
        "Does **not** claim AGI, consciousness, model superiority\n"
        "States Docker `:latest` will remain `v3.8.0`\n",
        encoding="utf-8",
    )
    return release_notes


def _rt_pip_report(root: Path, name: str, vulns: int) -> None:
    (root / name).write_text(
        json.dumps({
            "dependencies": [
                {
                    "name": "pkg",
                    "version": "1",
                    "vulns": [{"id": f"PYSEC-RT-{i}"} for i in range(vulns)],
                }
            ]
        }),
        encoding="utf-8",
    )


def _rt_bandit_report(root: Path, name: str, medium: int) -> None:
    (root / name).write_text(
        json.dumps({
            "metrics": {
                "_totals": {"SEVERITY.HIGH": 0, "SEVERITY.MEDIUM": medium},
            },
            "results": [],
        }),
        encoding="utf-8",
    )


def _rt_build_and_verify(tmp_path, evidence_root, release_notes, **explicit):
    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit=COMMIT,
        ended_at_utc=dt.datetime(2026, 5, 22, 12, 0, tzinfo=dt.UTC),
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
        **explicit,
    )
    soak_evidence = tmp_path / "v3.12.0.json"
    soak_evidence.write_text(json.dumps(evidence), encoding="utf-8")
    report = build_report(
        soak_evidence=soak_evidence,
        release_readiness="docs/release/RELEASE_READINESS.md",
        evidence_root=evidence_root,
        release_notes=release_notes,
    )
    return evidence, report


def test_verifier_round_trips_explicit_pip_selection(tmp_path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = _rt_release_notes(tmp_path)
    # Two candidates; the explicit selection pins one of them. A vulnerable
    # report keeps security blocked on BOTH sides, so the attestation
    # extension stays inactive and the test isolates pure round-tripping.
    _rt_pip_report(evidence_root, _RT_PIP_OLD, vulns=1)
    _rt_pip_report(evidence_root, _RT_PIP_NEW, vulns=0)

    evidence, report = _rt_build_and_verify(
        tmp_path, evidence_root, release_notes, pip_audit_report=_RT_PIP_OLD
    )

    assert (
        evidence["artifact_selection"]["pip_audit_report"]["basis"]
        == "explicit"
    )
    assert report["mismatched_fields"] == []
    assert report["blockers"] == []
    assert report["verified"] is True


def test_verifier_round_trips_explicit_bandit_selection(tmp_path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = _rt_release_notes(tmp_path)
    _rt_bandit_report(evidence_root, _RT_BANDIT_ZM, medium=0)
    _rt_bandit_report(evidence_root, _RT_BANDIT_PLAIN, medium=1)
    _rt_pip_report(evidence_root, _RT_PIP_NEW, vulns=1)

    evidence, report = _rt_build_and_verify(
        tmp_path,
        evidence_root,
        release_notes,
        bandit_report=_RT_BANDIT_PLAIN,
    )

    assert (
        evidence["artifact_selection"]["bandit_report"]["basis"] == "explicit"
    )
    assert report["mismatched_fields"] == []
    assert report["blockers"] == []
    assert report["verified"] is True


@pytest.mark.parametrize(
    "bad_path",
    [
        "../outside.json",
        "/abs/evil.json",
        "C:/evil.json",
        "a\\b.json",
        "",
        None,
    ],
    ids=["dotdot", "posix-abs", "drive-abs", "backslash", "empty", "none"],
)
def test_verifier_rejects_non_root_relative_explicit_selection(
    tmp_path, bad_path
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = _rt_release_notes(tmp_path)
    _rt_pip_report(evidence_root, _RT_PIP_NEW, vulns=1)

    evidence, _ = _rt_build_and_verify(tmp_path, evidence_root, release_notes)
    evidence["artifact_selection"]["pip_audit_report"] = {
        "basis": "explicit",
        "path": bad_path,
        "source_digest": "sha256:0",
        "candidates": [],
        "blockers": [],
    }
    soak_evidence = tmp_path / "tampered.json"
    soak_evidence.write_text(json.dumps(evidence), encoding="utf-8")

    report = build_report(
        soak_evidence=soak_evidence,
        release_readiness="docs/release/RELEASE_READINESS.md",
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert report["verified"] is False
    assert report["blockers"] == [
        "explicit_selection_invalid:pip_audit_report"
    ]


# ---------------------------------------------------------------------------
# Transitive source-substitution regressions (Grok/lead changes_requested
# 2026-08-26T08:02:27Z at head 11985868): the attestation must re-validate
# EXACTLY the containment-validated selected pip artifact. A registry-order
# first-existing fallback lets the verifier attest file A while the collector
# selected, digest-bound and gated on file B.

_SUB_FIRST = FINAL_PIP_AUDIT_REPORTS[0]
_SUB_MIDDLE = FINAL_PIP_AUDIT_REPORTS[2]
_SUB_LAST = FINAL_PIP_AUDIT_REPORTS[-1]
# Satisfies BOTH the collector precheck ("74 passed" + SMOKE_OK) and the
# attestation floor, so a pass claim is not masked by a privacy blocker.
_SUB_PRIVACY = "# v3.12.0 final privacy receipt\n\n74 passed\nSMOKE_OK\n"


def _lock_matching_dependencies(*, with_vulns: bool = False):
    deps = _real_lock_report_dependencies()
    if with_vulns:
        return [dict(dep, vulns=[]) for dep in deps]
    return deps


def _drifted_dependencies(*, with_vulns: bool = False):
    entry = {"name": "aiohttp", "version": "0.0.1"}
    return [dict(entry, vulns=[])] if with_vulns else [entry]


def _write_pip_candidate(root: Path, name: str, deps, mtime: int) -> None:
    path = root / name
    path.write_text(json.dumps({"dependencies": deps}), encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _sub_env(tmp_path) -> Path:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    (evidence_root / PRIVACY_PRECHECK).write_text(
        _SUB_PRIVACY, encoding="utf-8"
    )
    return evidence_root


def test_attestation_consumes_selected_artifact_not_registry_first(
    tmp_path,
) -> None:
    # Registry-FIRST candidate matches the lock (attestation would clear);
    # the NEWEST candidate - the one the collector actually selects and
    # gates on - is stale. Under the old first-existing scan the verifier
    # attested the clean file and the stale one passed unexamined.
    evidence_root = _sub_env(tmp_path)
    _write_pip_candidate(
        evidence_root, _SUB_FIRST, _lock_matching_dependencies(), 1_000_000
    )
    _write_pip_candidate(
        evidence_root, _SUB_LAST, _drifted_dependencies(), 2_000_000
    )

    report = _attestation_report(
        tmp_path, evidence_root, security_privacy_gate="pass"
    )

    assert "audited_lock_pins_stale" in report["blockers"]
    assert report["verified"] is False


def test_attestation_clears_when_selected_artifact_matches_lock(
    tmp_path,
) -> None:
    # Converse direction: the STALE file is the registry-first one and the
    # selected newest file matches the lock. Attestation must clear - proof
    # the binding follows selection, not merely "adds more blockers".
    evidence_root = _sub_env(tmp_path)
    _write_pip_candidate(
        evidence_root, _SUB_FIRST, _drifted_dependencies(), 1_000_000
    )
    _write_pip_candidate(
        evidence_root, _SUB_LAST, _lock_matching_dependencies(), 2_000_000
    )

    report = _attestation_report(
        tmp_path, evidence_root, security_privacy_gate="pass"
    )

    assert not (set(report["blockers"]) & ATTESTATION_BLOCKERS)


def test_attestation_consumes_explicit_selection_over_newest_and_registry(
    tmp_path,
) -> None:
    # Explicit pass-claim round trip: registry-first AND newest are both
    # stale; only the explicitly selected middle candidate matches the
    # lock. Any file choice other than the recovered explicit one fails.
    evidence_root = _sub_env(tmp_path)
    release_notes = tmp_path / "v3.12.0.md"
    release_notes.write_text(
        "Does **not** claim AGI, consciousness, model superiority\n"
        "States Docker `:latest` will remain `v3.8.0`\n",
        encoding="utf-8",
    )
    (evidence_root / "v3.12.0_bandit_report.json").write_text(
        json.dumps({
            "metrics": {"_totals": {"SEVERITY.HIGH": 0, "SEVERITY.MEDIUM": 0}},
            "results": [],
        }),
        encoding="utf-8",
    )
    _write_pip_candidate(
        evidence_root,
        _SUB_FIRST,
        _drifted_dependencies(with_vulns=True),
        1_000_000,
    )
    _write_pip_candidate(
        evidence_root,
        _SUB_MIDDLE,
        _lock_matching_dependencies(with_vulns=True),
        1_500_000,
    )
    _write_pip_candidate(
        evidence_root,
        _SUB_LAST,
        _drifted_dependencies(with_vulns=True),
        2_000_000,
    )

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit=COMMIT,
        ended_at_utc=dt.datetime(2026, 5, 22, 12, 0, tzinfo=dt.UTC),
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
        pip_audit_report=_SUB_MIDDLE,
    )
    assert (
        evidence["artifact_selection"]["pip_audit_report"]["basis"]
        == "explicit"
    )
    assert evidence["security_privacy_gate"] == "pass"
    soak_evidence = tmp_path / "v3.12.0.json"
    soak_evidence.write_text(json.dumps(evidence), encoding="utf-8")

    report = build_report(
        soak_evidence=soak_evidence,
        release_readiness="docs/release/RELEASE_READINESS.md",
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert not (set(report["blockers"]) & ATTESTATION_BLOCKERS)
    assert report["mismatched_fields"] == []
    assert report["verified"] is True


def test_attestation_fails_closed_when_selection_is_ambiguous(
    tmp_path,
) -> None:
    # Two candidates with identical mtimes: selection itself fails, so the
    # attestation must refuse rather than attest an arbitrary candidate.
    evidence_root = _sub_env(tmp_path)
    _write_pip_candidate(
        evidence_root, _SUB_FIRST, _lock_matching_dependencies(), 1_500_000
    )
    _write_pip_candidate(
        evidence_root, _SUB_LAST, _lock_matching_dependencies(), 1_500_000
    )

    report = _attestation_report(
        tmp_path, evidence_root, security_privacy_gate="pass"
    )

    assert "audited_report_selection_blocked" in report["blockers"]
    assert "audited_report_missing" in report["blockers"]
    assert report["verified"] is False


def test_verifier_blocks_tampered_selection_source_digest(tmp_path) -> None:
    evidence_root = _sub_env(tmp_path)
    release_notes = tmp_path / "v3.12.0.md"
    release_notes.write_text("notes\n", encoding="utf-8")
    _write_pip_candidate(
        evidence_root, _SUB_LAST, _lock_matching_dependencies(), 2_000_000
    )

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit=COMMIT,
        ended_at_utc=dt.datetime(2026, 5, 22, 12, 0, tzinfo=dt.UTC),
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )
    record = evidence["artifact_selection"]["pip_audit_report"]
    assert record["source_digest"].startswith("sha256:")
    record["source_digest"] = "sha256:" + "0" * 64
    soak_evidence = tmp_path / "tampered_digest.json"
    soak_evidence.write_text(json.dumps(evidence), encoding="utf-8")

    report = build_report(
        soak_evidence=soak_evidence,
        release_readiness="docs/release/RELEASE_READINESS.md",
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert "field_mismatch:artifact_selection" in report["blockers"]
    assert report["verified"] is False


@pytest.mark.parametrize(
    "bad_path",
    [
        "\\\\server\\share\\report.json",
        "//server/share/report.json",
        "sub/../../evil.json",
        "nested/deeper/../../../evil.json",
        "./../evil.json",
    ],
    ids=["unc-backslash", "unc-slash", "nested-traversal", "deep-traversal",
         "dot-prefixed-traversal"],
)
def test_verifier_rejects_unc_and_nested_traversal_records(
    tmp_path, bad_path
) -> None:
    evidence_root = _sub_env(tmp_path)
    _write_pip_candidate(
        evidence_root, _SUB_LAST, _drifted_dependencies(), 2_000_000
    )
    release_notes = tmp_path / "v3.12.0.md"
    release_notes.write_text("notes\n", encoding="utf-8")

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit=COMMIT,
        ended_at_utc=dt.datetime(2026, 5, 22, 12, 0, tzinfo=dt.UTC),
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )
    evidence["artifact_selection"]["pip_audit_report"] = {
        "basis": "explicit",
        "path": bad_path,
        "source_digest": "sha256:0",
        "candidates": [],
        "blockers": [],
    }
    soak_evidence = tmp_path / "traversal.json"
    soak_evidence.write_text(json.dumps(evidence), encoding="utf-8")

    report = build_report(
        soak_evidence=soak_evidence,
        release_readiness="docs/release/RELEASE_READINESS.md",
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert report["verified"] is False
    assert report["blockers"] == [
        "explicit_selection_invalid:pip_audit_report"
    ]
