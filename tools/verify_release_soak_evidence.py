#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify release soak evidence is reproducible from local artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.collect_soak_evidence import (
    DEFAULT_EVIDENCE_ROOT,
    DEFAULT_RELEASE_NOTES,
    build_soak_evidence,
)


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

    try:
        expected = build_soak_evidence(
            release_readiness,
            commit=str(actual.get("commit", "")),
            started_at_utc=_parse_timestamp(actual.get("started_at_utc")),
            ended_at_utc=_parse_timestamp(actual.get("ended_at_utc")),
            use_local_artifacts=True,
            evidence_root=evidence_root,
            release_notes=release_notes,
        )
    except (OSError, ValueError) as exc:
        return {
            "schema_version": "waggledance.release_soak_verifier.v1",
            "verified": False,
            "blockers": [f"expected_evidence_unbuildable:{exc.__class__.__name__}"],
            "mismatched_fields": [],
        }

    for field in sorted(set(actual) | set(expected)):
        if actual.get(field) != expected.get(field):
            mismatched_fields.append(field)
            blockers.append(f"field_mismatch:{field}")

    return {
        "schema_version": "waggledance.release_soak_verifier.v1",
        "verified": not blockers,
        "blockers": blockers,
        "mismatched_fields": mismatched_fields,
        "soak_evidence": soak_evidence.as_posix(),
        "release_readiness": release_readiness.as_posix(),
        "evidence_root": evidence_root.as_posix(),
        "release_notes": release_notes.as_posix(),
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
