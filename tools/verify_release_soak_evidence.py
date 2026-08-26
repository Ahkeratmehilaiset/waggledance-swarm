#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify release soak evidence is reproducible from local artifacts.

When the actual evidence records an explicit security-artifact selection
(``artifact_selection`` entry with ``basis == "explicit"``), the rebuild
recovers that selection - but only a validated root-relative recorded
path is accepted (typed ``explicit_selection_invalid:<field>`` blocker
otherwise). The recovered path re-enters the collector's containment
checks against the resolved evidence root, so recovery cannot escape the
root or fall open.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.collect_soak_evidence import (
    DEFAULT_EVIDENCE_ROOT,
    DEFAULT_RELEASE_NOTES,
    FINAL_PIP_AUDIT_REPORTS,
    PRIVACY_PRECHECK,
    SELECTION_BASIS_EXPLICIT,
    build_soak_evidence,
)
from tools.release_security_attestation import (
    evaluate_audited_lock_pins,
    evaluate_privacy_attestation,
)

_MISSING = object()

_ATTESTATION_CLAIM_FIELDS = ("profile_s_smoke", "security_privacy_gate")


def _security_attestation_blockers(
    actual: dict[str, Any],
    expected: dict[str, Any],
    evidence_root: Path,
) -> list[str]:
    """Fail-closed attestation blockers, active only under a pass claim.

    When neither the actual evidence nor the rebuilt expected evidence
    claims ``profile_s_smoke`` or ``security_privacy_gate`` pass, this
    returns no blockers and legacy behavior is unchanged.
    """
    claims_pass = any(
        actual.get(field) == "pass" or expected.get(field) == "pass"
        for field in _ATTESTATION_CLAIM_FIELDS
    )
    if not claims_pass:
        return []
    blockers = list(
        evaluate_privacy_attestation(evidence_root / PRIVACY_PRECHECK)
    )
    audited_report = None
    for name in FINAL_PIP_AUDIT_REPORTS:
        candidate = evidence_root / name
        if candidate.exists():
            audited_report = candidate
            break
    blockers.extend(evaluate_audited_lock_pins(audited_report))
    return blockers


def _parse_timestamp(value: object) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp missing")
    normalized = value.strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _read_object(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("evidence JSON must be an object")
    return loaded


def _validated_root_relative(value: object) -> str | None:
    """Accept only a safe root-relative posix path recorded by the collector.

    Rejects non-strings, empties, backslashes, absolute paths (posix or
    Windows drive/UNC forms), and any ``..`` traversal component. The
    recovered value re-enters ``_select_artifact``, which containment-checks
    the resolved path against the resolved evidence root and fails closed;
    this validator just refuses obviously non-root-relative records up
    front with a typed blocker instead of rebuilding a divergent expected.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    if "\\" in value:
        return None
    pure = PurePosixPath(value)
    if pure.is_absolute():
        return None
    if ":" in value or value.startswith("//"):
        return None
    if any(part == ".." for part in pure.parts):
        return None
    return value


def build_report(
    *,
    soak_evidence: Path | str,
    release_readiness: Path | str,
    evidence_root: Path | str = DEFAULT_EVIDENCE_ROOT,
    release_notes: Path | str = DEFAULT_RELEASE_NOTES,
) -> dict[str, Any]:
    blockers: list[str] = []
    mismatched_fields: list[str] = []
    soak_evidence = Path(soak_evidence)
    release_readiness = Path(release_readiness)
    evidence_root = Path(evidence_root)
    release_notes = Path(release_notes)
    try:
        actual = _read_object(soak_evidence)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "schema_version": "waggledance.release_soak_verifier.v1",
            "verified": False,
            "blockers": [f"soak_evidence_unreadable:{exc.__class__.__name__}"],
            "mismatched_fields": [],
        }

    explicit_overrides: dict[str, str] = {}
    selection = actual.get("artifact_selection")
    if isinstance(selection, dict):
        for field, kwarg in (
            ("bandit_report", "bandit_report"),
            ("pip_audit_report", "pip_audit_report"),
        ):
            record = selection.get(field)
            if not isinstance(record, dict):
                continue
            if record.get("basis") != SELECTION_BASIS_EXPLICIT:
                continue
            recovered = _validated_root_relative(record.get("path"))
            if recovered is None:
                return {
                    "schema_version": "waggledance.release_soak_verifier.v1",
                    "verified": False,
                    "blockers": [f"explicit_selection_invalid:{field}"],
                    "mismatched_fields": [],
                }
            explicit_overrides[kwarg] = recovered

    try:
        expected = build_soak_evidence(
            release_readiness,
            commit=str(actual.get("commit", "")),
            started_at_utc=_parse_timestamp(actual.get("started_at_utc")),
            ended_at_utc=_parse_timestamp(actual.get("ended_at_utc")),
            use_local_artifacts=True,
            evidence_root=evidence_root,
            release_notes=release_notes,
            **explicit_overrides,
        )
    except (OSError, ValueError) as exc:
        return {
            "schema_version": "waggledance.release_soak_verifier.v1",
            "verified": False,
            "blockers": [f"expected_evidence_unbuildable:{exc.__class__.__name__}"],
            "mismatched_fields": [],
        }

    for field in sorted(set(actual) | set(expected)):
        actual_value = actual[field] if field in actual else _MISSING
        expected_value = expected[field] if field in expected else _MISSING
        if actual_value != expected_value:
            mismatched_fields.append(field)
            blockers.append(f"field_mismatch:{field}")

    blockers.extend(
        _security_attestation_blockers(actual, expected, evidence_root)
    )

    return {
        "schema_version": "waggledance.release_soak_verifier.v1",
        "verified": not blockers,
        "blockers": blockers,
        "mismatched_fields": mismatched_fields,
        "soak_evidence": "<redacted>",
        "release_readiness": "<redacted>",
        "evidence_root": "<redacted>",
        "release_notes": "<redacted>",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--soak-evidence",
        default=Path("docs/runs/release_soak_evidence/v3.12.0.json"),
        type=Path,
    )
    parser.add_argument(
        "--release-readiness",
        default=Path("docs/release/RELEASE_READINESS.md"),
        type=Path,
    )
    parser.add_argument("--evidence-root", default=DEFAULT_EVIDENCE_ROOT, type=Path)
    parser.add_argument("--release-notes", default=DEFAULT_RELEASE_NOTES, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    report = build_report(
        soak_evidence=args.soak_evidence,
        release_readiness=args.release_readiness,
        evidence_root=args.evidence_root,
        release_notes=args.release_notes,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
