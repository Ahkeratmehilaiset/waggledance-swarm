#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only counter-read of a MAGMA 100h sprint baseline.json.

Phase C tool-build of the 100h plan (``valiant-beaming-rocket.md``).
Implements the structurally-separated independent audit role that the
sprint baseline encodes as ``claude_activation_contract.required_roles``
(``independent_audit + adversarial_review + competitor_counter_read``).

Two modes:

1. **Static mode** (default): given a baseline.json path, check a fixed
   set of charter invariants -- no overclaim, release-boundary safe,
   forbidden_claims preserved, A3/A4 labels qualified, competitor
   consensus_grade explicitly False.

2. **Delta mode** (``--against PATH``): given two baseline.json files
   (a known-honest anchor and a candidate slice), additionally check
   that the candidate does NOT silently flip qualified labels to
   unqualified, NOR remove any forbidden_claims entry, NOR mutate any
   ``release_boundary`` flag.

The output is a PASS/BLOCK JSON document; exits non-zero on any BLOCK.
The tool itself never edits the baseline. Use it before signing off on
any MAGMA-sprint slice PR.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_BASELINE = Path("docs/runs/magma_100h_sprint_2026_05_23/baseline.json")

# Charter invariants (constant across all MAGMA-sprint slices unless the
# operator signs a decision pack relaxing one). Each lives at a specific
# path inside baseline.json; the tool checks both presence and value.
EXPECTED_SCHEMA_VERSION = "waggledance.magma_100h_sprint_baseline.v0"

EXPECTED_RELEASE_BOUNDARY_ALL_FALSE = (
    "docker_latest_move",
    "external_effect_authority_change",
    "stable_release_claim",
    "tag_creation",
)

REQUIRED_FORBIDDEN_CLAIMS = (
    "AGI",
    "consciousness",
    "public cryptographic verification parity",
    "rival benchmark consensus-grade",
)

# Allowed (qualified) label values for the must-win axes. A flip to any
# value outside this set is treated as an upgrade-overclaim that needs
# adjudicated evidence -- the counter-read flags it for review.
A3_QUALIFIED_LABELS = frozenset(
    {
        "PUBLIC_DOC_CLAIM",
        "MEASURED_LOCAL_PARTIAL",
        "MEASURED_LOCAL",
        "MEASURED_NETWORK",
    }
)
A4_QUALIFIED_LABELS = frozenset(
    {
        "PUBLIC_DOC_CLAIM",
        "MEASURED_LOCAL_SYNTHETIC",
        "MEASURED_LOCAL",
        "MEASURED_NETWORK",
    }
)

# Labels that count as "unqualified" -- a slice that flips A3 or A4 to
# any of these without an adjudicated benchmark is an overclaim.
UNQUALIFIED_LABELS = frozenset({"PROVEN", "CONSENSUS_GRADE", "FULL", "GA"})


def _read_baseline(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _path(obj: Any, *keys: str) -> Any:
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def check_invariants(baseline: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a list of finding dicts; an empty list means PASS.

    Each finding: ``{path, severity, code, expected, actual, message}``.
    Severity is always ``"block"`` for invariant violations. Use this as
    the building block for static and delta mode reports.
    """

    findings: list[dict[str, Any]] = []

    actual_schema = baseline.get("schema_version")
    if actual_schema != EXPECTED_SCHEMA_VERSION:
        findings.append(
            {
                "path": "schema_version",
                "severity": "block",
                "code": "schema_version_unexpected",
                "expected": EXPECTED_SCHEMA_VERSION,
                "actual": actual_schema,
                "message": (
                    "schema_version must match the locked baseline schema; "
                    "any change requires a separate migration RFC."
                ),
            }
        )

    rb = baseline.get("release_boundary") or {}
    if not isinstance(rb, dict):
        findings.append(
            {
                "path": "release_boundary",
                "severity": "block",
                "code": "release_boundary_not_object",
                "expected": "object with 4 boolean flags",
                "actual": type(rb).__name__,
                "message": "release_boundary must be an object.",
            }
        )
    else:
        for flag in EXPECTED_RELEASE_BOUNDARY_ALL_FALSE:
            value = rb.get(flag)
            if value is not False:
                findings.append(
                    {
                        "path": f"release_boundary.{flag}",
                        "severity": "block",
                        "code": "release_boundary_flag_must_be_false",
                        "expected": False,
                        "actual": value,
                        "message": (
                            f"release_boundary.{flag} is operator-only "
                            "and must remain false during sprint slices."
                        ),
                    }
                )

    fc = baseline.get("forbidden_claims") or []
    if not isinstance(fc, list):
        findings.append(
            {
                "path": "forbidden_claims",
                "severity": "block",
                "code": "forbidden_claims_not_list",
                "expected": "list of forbidden claim strings",
                "actual": type(fc).__name__,
                "message": "forbidden_claims must be a list.",
            }
        )
    else:
        for required in REQUIRED_FORBIDDEN_CLAIMS:
            if required not in fc:
                findings.append(
                    {
                        "path": "forbidden_claims",
                        "severity": "block",
                        "code": "required_forbidden_claim_missing",
                        "expected": required,
                        "actual": fc,
                        "message": (
                            f"forbidden_claims must contain {required!r}; "
                            "removing it would permit overclaim."
                        ),
                    }
                )

    cp = _path(baseline, "current_state", "competitor_pilot")
    if isinstance(cp, dict):
        if cp.get("consensus_grade") is not False:
            findings.append(
                {
                    "path": "current_state.competitor_pilot.consensus_grade",
                    "severity": "block",
                    "code": "competitor_consensus_grade_must_be_false",
                    "expected": False,
                    "actual": cp.get("consensus_grade"),
                    "message": (
                        "consensus_grade may only flip true with an "
                        "adjudicated rival benchmark; not in scope for "
                        "any current sprint slice."
                    ),
                }
            )
        rl_cg = cp.get("rival_local_check_consensus_grade")
        if rl_cg is not None and rl_cg is not False:
            findings.append(
                {
                    "path": (
                        "current_state.competitor_pilot."
                        "rival_local_check_consensus_grade"
                    ),
                    "severity": "block",
                    "code": "rival_local_check_consensus_grade_must_be_false",
                    "expected": False,
                    "actual": rl_cg,
                    "message": (
                        "rival_local_check_consensus_grade is the per-rival "
                        "aggregate; true would imply consensus-grade, which "
                        "is forbidden without adjudicated evidence."
                    ),
                }
            )

    a3 = _path(baseline, "current_state", "a3_counterfactual_axis")
    if isinstance(a3, dict):
        label = a3.get("claim_label")
        if label is not None and label not in A3_QUALIFIED_LABELS:
            findings.append(
                {
                    "path": "current_state.a3_counterfactual_axis.claim_label",
                    "severity": "block",
                    "code": "a3_claim_label_unqualified",
                    "expected": sorted(A3_QUALIFIED_LABELS),
                    "actual": label,
                    "message": (
                        "A3 claim_label must remain qualified "
                        f"({sorted(A3_QUALIFIED_LABELS)}). Upgrades to "
                        f"{sorted(UNQUALIFIED_LABELS)} require adjudicated "
                        "evidence + a separate decision pack."
                    ),
                }
            )

    a4 = _path(baseline, "current_state", "a4_solver_growth_axis")
    if isinstance(a4, dict):
        label = a4.get("claim_label")
        if label is not None and label not in A4_QUALIFIED_LABELS:
            findings.append(
                {
                    "path": "current_state.a4_solver_growth_axis.claim_label",
                    "severity": "block",
                    "code": "a4_claim_label_unqualified",
                    "expected": sorted(A4_QUALIFIED_LABELS),
                    "actual": label,
                    "message": (
                        "A4 claim_label must remain qualified "
                        f"({sorted(A4_QUALIFIED_LABELS)}). Upgrades to "
                        f"{sorted(UNQUALIFIED_LABELS)} require adjudicated "
                        "evidence + a separate decision pack."
                    ),
                }
            )

    return findings


def check_delta(
    anchor: dict[str, Any], candidate: dict[str, Any]
) -> list[dict[str, Any]]:
    """Delta-mode findings: things only visible when comparing two baselines."""

    findings: list[dict[str, Any]] = []

    anchor_fc = set(anchor.get("forbidden_claims") or [])
    candidate_fc = set(candidate.get("forbidden_claims") or [])
    removed = sorted(anchor_fc - candidate_fc)
    if removed:
        findings.append(
            {
                "path": "forbidden_claims",
                "severity": "block",
                "code": "forbidden_claim_removed_in_candidate",
                "expected": sorted(anchor_fc),
                "actual": sorted(candidate_fc),
                "message": (
                    f"Candidate removed forbidden_claims entries {removed}; "
                    "removals require a separate decision pack."
                ),
            }
        )

    anchor_rb = anchor.get("release_boundary") or {}
    candidate_rb = candidate.get("release_boundary") or {}
    for flag in EXPECTED_RELEASE_BOUNDARY_ALL_FALSE:
        if anchor_rb.get(flag) != candidate_rb.get(flag):
            findings.append(
                {
                    "path": f"release_boundary.{flag}",
                    "severity": "block",
                    "code": "release_boundary_flag_changed_in_candidate",
                    "expected": anchor_rb.get(flag),
                    "actual": candidate_rb.get(flag),
                    "message": (
                        f"release_boundary.{flag} changed between anchor "
                        "and candidate; release-boundary changes are "
                        "operator-only and must not land via a sprint slice."
                    ),
                }
            )

    a_label = _path(
        anchor, "current_state", "a3_counterfactual_axis", "claim_label"
    )
    c_label = _path(
        candidate, "current_state", "a3_counterfactual_axis", "claim_label"
    )
    if a_label != c_label and c_label in UNQUALIFIED_LABELS:
        findings.append(
            {
                "path": "current_state.a3_counterfactual_axis.claim_label",
                "severity": "block",
                "code": "a3_label_upgraded_to_unqualified",
                "expected": a_label,
                "actual": c_label,
                "message": (
                    "A3 claim_label upgraded from a qualified label to an "
                    "unqualified label; requires adjudicated evidence."
                ),
            }
        )

    a_label = _path(
        anchor, "current_state", "a4_solver_growth_axis", "claim_label"
    )
    c_label = _path(
        candidate, "current_state", "a4_solver_growth_axis", "claim_label"
    )
    if a_label != c_label and c_label in UNQUALIFIED_LABELS:
        findings.append(
            {
                "path": "current_state.a4_solver_growth_axis.claim_label",
                "severity": "block",
                "code": "a4_label_upgraded_to_unqualified",
                "expected": a_label,
                "actual": c_label,
                "message": (
                    "A4 claim_label upgraded from a qualified label to an "
                    "unqualified label; requires adjudicated evidence."
                ),
            }
        )

    return findings


def counter_read(
    baseline_path: Path,
    *,
    against: Path | None = None,
) -> dict[str, Any]:
    """Run the counter-read and return the structured report."""

    candidate = _read_baseline(baseline_path)
    findings = check_invariants(candidate)
    delta_findings: list[dict[str, Any]] = []
    if against is not None:
        anchor = _read_baseline(against)
        delta_findings = check_delta(anchor, candidate)

    all_findings = findings + delta_findings
    decision = "block" if all_findings else "pass"
    return {
        "decision": decision,
        "baseline_path": str(baseline_path),
        "anchor_path": str(against) if against else None,
        "findings_count": len(all_findings),
        "static_findings": findings,
        "delta_findings": delta_findings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help=(
            "MAGMA-sprint baseline.json to audit "
            f"(default: {DEFAULT_BASELINE})."
        ),
    )
    parser.add_argument(
        "--against",
        type=Path,
        default=None,
        help=(
            "Optional anchor baseline.json for delta mode. When provided, "
            "the tool also flags forbidden_claims removals and "
            "release_boundary flips between anchor and candidate."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the JSON report to this path (in addition to stdout).",
    )
    args = parser.parse_args(argv)

    if not args.baseline.exists():
        print(
            f"magma_slice_counter_read: baseline not found: {args.baseline}",
            file=sys.stderr,
        )
        return 2

    if args.against is not None and not args.against.exists():
        print(
            f"magma_slice_counter_read: anchor not found: {args.against}",
            file=sys.stderr,
        )
        return 2

    report = counter_read(args.baseline, against=args.against)
    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")

    return 0 if report["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
