#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record accepted blockers for non-passing rival-local checks.

This report does not promote any rival-local row. It records which blocked rows
have enough pinned local evidence to be accepted as explicit blockers while the
matrix remains non-consensus-grade.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_v12_rival_local_check_matrix import build_rival_local_check_matrix

SCHEMA_VERSION = "wd.v12.rival_local_accepted_blockers.v0"
DEFAULT_EVIDENCE_DIR = Path("docs/benchmarks/rival_local_checks")

EXPECTED_ACCEPTED_BLOCKERS = {
    "JamJet": {
        "accepted_blocker_reason": "policy_audit_or_replay_smoke_not_run",
        "blocked_artifact_reason": "smoke_result",
        "local_status": "not_passed",
        "required_next_action": (
            "Run a local offline policy/audit/replay smoke against the pinned "
            "JamJet artifact before it can contribute to consensus_grade."
        ),
    },
    "Asqav": {
        "accepted_blocker_reason": "cloud_dependent_headline_receipt",
        "blocked_artifact_reason": "cloud_dependency",
        "local_status": "cloud_dependent",
        "blocker_detail": (
            "The local Asqav artifact proves offline keypair generation and "
            "action queueing, but the headline ML-DSA-65 signature and signed "
            "receipt emission remain cloud-dependent."
        ),
        "required_next_action": (
            "Produce a local signed-receipt/hash-chain smoke with "
            "cloud_dependency=false, or keep Asqav as an accepted blocker."
        ),
    },
    "Preloop": {
        "accepted_blocker_reason": "mcp_allow_deny_approval_smoke_not_run",
        "blocked_artifact_reason": "smoke_result",
        "local_status": "not_passed",
        "required_next_action": (
            "Run a local offline MCP allow/deny/approval smoke against the "
            "pinned Preloop artifact before it can contribute to consensus_grade."
        ),
    },
}


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--now requires a UTC timestamp with Z or +00:00 suffix")
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _relative_path(path_value: object, evidence_root: Path) -> str | None:
    if not isinstance(path_value, str) or not path_value.strip():
        return None
    path = Path(path_value)
    try:
        relative = path.resolve().relative_to(evidence_root.resolve())
    except ValueError:
        return path.as_posix()
    return relative.as_posix()


def _row_by_rival(matrix: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = matrix.get("checks")
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("rival")): row
        for row in rows
        if isinstance(row, dict) and row.get("rival")
    }


def _accepted_blocker_entry(
    *,
    row: dict[str, Any],
    evidence_root: Path,
) -> tuple[dict[str, Any], list[str]]:
    rival = str(row.get("rival"))
    expected = EXPECTED_ACCEPTED_BLOCKERS[rival]
    blockers: list[str] = []
    proof = row.get("artifact_proof")
    if not isinstance(proof, dict):
        proof = {}
        blockers.append(f"{rival}:artifact_proof_missing")
    if row.get("local_status") != expected["local_status"]:
        blockers.append(f"{rival}:unexpected_local_status")
    if row.get("blocked_artifact_reason") != expected["blocked_artifact_reason"]:
        blockers.append(f"{rival}:unexpected_blocked_artifact_reason")
    if row.get("consensus_grade_contribution") is not False:
        blockers.append(f"{rival}:blocked_row_contributes_to_consensus_grade")
    if proof.get("artifact_digest_verified") is not True:
        blockers.append(f"{rival}:artifact_digest_not_verified")

    return (
        {
            "rival": rival,
            "local_status": row.get("local_status"),
            "blocker": row.get("blocker"),
            "blocked_artifact_reason": row.get("blocked_artifact_reason"),
            "accepted_blocker_reason": expected["accepted_blocker_reason"],
            "accepted_blocker": True,
            "consensus_grade_contribution": row.get(
                "consensus_grade_contribution"
            ),
            "artifact_digest_verified": proof.get("artifact_digest_verified"),
            "local_artifact_path": _relative_path(
                proof.get("local_artifact_path"), evidence_root
            ),
            "local_artifact_sha256": proof.get("local_artifact_sha256"),
            "blocker_detail": row.get("blocker_detail")
            or proof.get("artifact_blocker_detail")
            or expected.get("blocker_detail"),
            "artifact_observation_details": row.get(
                "artifact_observation_details"
            )
            or proof.get("artifact_observation_details")
            or {},
            "required_next_action": expected["required_next_action"],
        },
        blockers,
    )


def build_report_from_matrix(
    matrix: dict[str, Any],
    *,
    evidence_dir: Path | str = DEFAULT_EVIDENCE_DIR,
    generated_at_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at_utc = generated_at_utc or _utc_now()
    evidence_root = Path(evidence_dir)
    rows_by_rival = _row_by_rival(matrix)
    accepted_blockers: list[dict[str, Any]] = []
    blockers: list[str] = []

    for rival in EXPECTED_ACCEPTED_BLOCKERS:
        row = rows_by_rival.get(rival)
        if row is None:
            blockers.append(f"{rival}:matrix_row_missing")
            continue
        entry, row_blockers = _accepted_blocker_entry(
            row=row,
            evidence_root=evidence_root,
        )
        accepted_blockers.append(entry)
        blockers.extend(row_blockers)

    if matrix.get("consensus_grade") is not False:
        blockers.append("matrix_consensus_grade_must_remain_false")
    if matrix.get("passed_count") != 1:
        blockers.append("matrix_passed_count_must_remain_1")
    if matrix.get("blocked_count") != 3:
        blockers.append("matrix_blocked_count_must_remain_3")

    return {
        "schema_version": SCHEMA_VERSION,
        "ok": not blockers,
        "generated_at_utc": _format_utc(generated_at_utc),
        "evidence_dir": Path(evidence_dir).as_posix(),
        "matrix_report_version": matrix.get("report_version"),
        "matrix_generated_at_utc": matrix.get("generated_at_utc"),
        "rival_local_checks_status": matrix.get("rival_local_checks_status"),
        "required_count": matrix.get("required_count"),
        "passed_count": matrix.get("passed_count"),
        "blocked_count": matrix.get("blocked_count"),
        "accepted_blocker_count": len(accepted_blockers),
        "consensus_grade": matrix.get("consensus_grade"),
        "accepted_blockers": accepted_blockers,
        "blockers": blockers,
        "no_overclaim_guardrails": {
            "does_not_promote_blocked_rows": True,
            "requires_artifact_digest_verified": True,
            "blocked_rows_do_not_contribute_to_consensus_grade": True,
            "consensus_grade_remains_false": matrix.get("consensus_grade") is False,
        },
    }


def build_report(
    *,
    evidence_dir: Path | str = DEFAULT_EVIDENCE_DIR,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    now_utc = now_utc or _utc_now()
    matrix = build_rival_local_check_matrix(
        evidence_dir=Path(evidence_dir),
        now_utc=now_utc,
    )
    return build_report_from_matrix(
        matrix,
        evidence_dir=evidence_dir,
        generated_at_utc=now_utc,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", default=DEFAULT_EVIDENCE_DIR, type=Path)
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic output.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        now_utc = _parse_utc(args.now) if args.now else None
        report = build_report(evidence_dir=args.evidence_dir, now_utc=now_utc)
    except ValueError as exc:
        print(f"rival local accepted blockers FAILED: {exc}", file=sys.stderr)
        return 1

    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    if args.json or args.output is None:
        print(encoded, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
