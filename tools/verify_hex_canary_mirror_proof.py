# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed verifier for hex canary mirror proof artifacts.

Companion to tools/run_hex_canary_mirror_proof.py: a consumer must be
able to re-derive the proof verdict from the artifact's fields instead
of trusting its ``ok`` flag. This verifier refuses on ANY deviation:

Consistency mode (always):
* exact report_version / claim_label / schema_version / measurement_scope
* every claim gate literal False and every authority flag at its only
  legal value — strict identity checks (``is True`` / ``is False``),
  never bool() coercion, so string/int forgeries refuse
* canonical_digest recomputes over the mirror-report core fields
* closed classification key set; all counts are real ints (bool is
  rejected explicitly) and internally consistent: by_classification,
  by_mesh_cell and by_mesh_method each sum to sample_count;
  agreement_count matches the match-classification counts;
  divergence_count and agreement_rate re-derive exactly
* ``ok`` itself re-derives from sample_count and the advisory floor

Anchor mode (--decisions): internal consistency alone cannot catch a
self-consistent tamper (edit a count AND recompute the digest). With
the original decisions JSONL, the verifier re-runs the mirror through
the real topology and requires the rebuilt mirror report to be
field-for-field identical — the external anchor.

Read-only, offline, deterministic. Output carries all claim gates false.
Exit codes: 0 verified, 1 verification failed (findings listed),
2 invalid arguments/unreadable input, 3 artifact file missing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.hex_topology.canary_mirror import (  # noqa: E402
    CANARY_CLASSIFICATIONS,
    CANARY_MATCH_INTENT_CELL,
    CANARY_MATCH_PRODUCTION_CELL,
    CANARY_MIRROR_REPORT_SCHEMA,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from tools.run_hex_canary_mirror_proof import (  # noqa: E402
    CLAIM_GATES,
    CLAIM_LABEL,
    MAX_SOURCE_LABEL_CHARS,
    REPORT_VERSION,
    build_canary_mirror_proof,
    _read_decisions,
)

# Authority flags and their only legal values on the nested mirror report
# (mirrors canary_mirror._AUTHORITY_FLAG_VALUES; duplicated as a frozen
# verification contract so a core regression cannot silently relax us).
REPORT_AUTHORITY_FLAGS: dict[str, bool] = {
    "no_runtime_mutation": True,
    "runtime_authority_granted": False,
    "routing_influence_applied": False,
    "production_decision_unchanged": True,
}
_MATCH_KEYS = (CANARY_MATCH_PRODUCTION_CELL, CANARY_MATCH_INTENT_CELL)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed re-derivation of a hex canary mirror proof "
            "artifact. Exit 0 only when every field re-derives."
        ),
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        required=True,
        help="Path to the proof artifact JSON written by the proof runner.",
    )
    parser.add_argument(
        "--decisions",
        type=Path,
        default=None,
        help=(
            "Optional original decisions JSONL. When given, the mirror is "
            "re-run and the rebuilt report must match field-for-field "
            "(external anchor against self-consistent tamper)."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON result to stdout"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.artifact.exists():
        print(f"artifact file not found: {args.artifact}", file=sys.stderr)
        return 3
    try:
        artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"artifact unreadable: {exc}", file=sys.stderr)
        return 2

    decisions = None
    if args.decisions is not None:
        if not args.decisions.exists():
            print(f"decisions file not found: {args.decisions}", file=sys.stderr)
            return 2
        try:
            decisions = _read_decisions(args.decisions)
        except ValueError as exc:
            print(f"decisions unreadable: {exc}", file=sys.stderr)
            return 2

    result = verify_canary_mirror_proof(artifact, decisions=decisions)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        verdict = "VERIFIED" if result["verified"] else "REFUSED"
        print(f"canary mirror proof {verdict} ({len(result['findings'])} findings)")
        for finding in result["findings"]:
            print(f"  - {finding}", file=sys.stderr)
    return 0 if result["verified"] else 1


def verify_canary_mirror_proof(
    artifact: Any,
    *,
    decisions: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Re-derive the proof verdict; collect every deviation as a finding."""
    findings: list[str] = []

    if not isinstance(artifact, Mapping):
        return _result(["artifact_not_object: artifact must be a JSON object"])

    if artifact.get("report_version") != REPORT_VERSION:
        findings.append("report_version_mismatch")
    if artifact.get("claim_label") != CLAIM_LABEL:
        findings.append("claim_label_mismatch")
    for gate in CLAIM_GATES:
        if artifact.get(gate) is not False:
            findings.append(f"claim_gate_not_false: {gate}")

    source = artifact.get("input_source")
    if not isinstance(source, str) or len(source) > MAX_SOURCE_LABEL_CHARS:
        findings.append("input_source_invalid")

    report = artifact.get("mirror_report")
    if not isinstance(report, Mapping):
        findings.append("mirror_report_missing")
        return _result(findings)

    findings.extend(_verify_report(report))

    sample_count = report.get("sample_count")
    if _is_int(artifact.get("input_record_count")) and _is_int(sample_count):
        if artifact["input_record_count"] != sample_count:
            findings.append("input_record_count_mismatch")
    else:
        findings.append("input_record_count_invalid")

    findings.extend(_verify_ok_rederivation(artifact, report))

    if decisions is not None:
        findings.extend(_verify_against_decisions(report, decisions))

    return _result(findings)


def _verify_report(report: Mapping[str, Any]) -> list[str]:
    findings: list[str] = []
    if report.get("schema_version") != CANARY_MIRROR_REPORT_SCHEMA:
        findings.append("report_schema_version_mismatch")
    if report.get("measurement_scope") != "shadow_mirror_read_only":
        findings.append("measurement_scope_mismatch")
    for flag, legal in REPORT_AUTHORITY_FLAGS.items():
        if report.get(flag) is not legal:
            findings.append(f"authority_flag_drift: {flag}")

    # Digest must recompute over every core field (order-independent dict).
    digest = report.get("canonical_digest")
    core = {k: v for k, v in report.items() if k != "canonical_digest"}
    if not isinstance(digest, str) or digest != sha256_digest(core):
        findings.append("canonical_digest_mismatch")

    by_classification = report.get("by_classification")
    if (
        not isinstance(by_classification, Mapping)
        or set(by_classification.keys()) != set(CANARY_CLASSIFICATIONS)
        or not all(_is_int(v) and v >= 0 for v in by_classification.values())
    ):
        findings.append("by_classification_invalid")
        return findings  # downstream arithmetic would be meaningless

    sample_count = report.get("sample_count")
    agreement_count = report.get("agreement_count")
    divergence_count = report.get("divergence_count")
    if not all(
        _is_int(v) and v >= 0
        for v in (sample_count, agreement_count, divergence_count)
    ):
        findings.append("count_fields_invalid")
        return findings

    if sum(by_classification.values()) != sample_count:
        findings.append("classification_sum_mismatch")
    expected_agreement = sum(by_classification[k] for k in _MATCH_KEYS)
    if agreement_count != expected_agreement:
        findings.append("agreement_count_mismatch")
    if divergence_count != sample_count - agreement_count:
        findings.append("divergence_count_mismatch")

    rate = report.get("agreement_rate")
    expected_rate = agreement_count / sample_count if sample_count else 0.0
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        findings.append("agreement_rate_invalid")
    elif rate != expected_rate:
        findings.append("agreement_rate_mismatch")

    for key in ("by_mesh_cell", "by_mesh_method"):
        bucket = report.get(key)
        if (
            not isinstance(bucket, Mapping)
            or not all(_is_int(v) and v > 0 for v in bucket.values())
            or sum(bucket.values()) != sample_count
        ):
            findings.append(f"{key}_sum_mismatch")
    return findings


def _verify_ok_rederivation(
    artifact: Mapping[str, Any], report: Mapping[str, Any]
) -> list[str]:
    findings: list[str] = []
    ok = artifact.get("ok")
    below = artifact.get("below_agreement_floor")
    floor = artifact.get("min_agreement_rate")
    if ok is not True and ok is not False:
        findings.append("ok_not_strict_bool")
        return findings
    if below is not True and below is not False:
        findings.append("below_floor_not_strict_bool")
        return findings

    rate = report.get("agreement_rate")
    sample_count = report.get("sample_count")
    if floor is not None:
        if (
            isinstance(floor, bool)
            or not isinstance(floor, (int, float))
            or not 0.0 <= float(floor) <= 1.0
        ):
            findings.append("min_agreement_rate_invalid")
            return findings
        if isinstance(rate, (int, float)) and not isinstance(rate, bool):
            if below is not (rate < floor):
                findings.append("below_floor_rederivation_mismatch")
    elif below is not False:
        findings.append("below_floor_without_floor")

    if _is_int(sample_count):
        expected_ok = sample_count > 0 and below is False
        if ok is not expected_ok:
            findings.append("ok_rederivation_mismatch")
    return findings


def _verify_against_decisions(
    report: Mapping[str, Any],
    decisions: Sequence[Mapping[str, Any]],
) -> list[str]:
    """External anchor: re-run the mirror and require an identical report."""
    try:
        from datetime import datetime, timezone

        rebuilt = build_canary_mirror_proof(
            decisions=decisions,
            source_label="rederivation",
            now=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )["mirror_report"]
    except Exception as exc:  # CanaryMirrorError / ValueError
        return [f"decisions_rederivation_failed: {exc}"]
    if dict(report) != rebuilt:
        return ["mirror_report_does_not_rederive_from_decisions"]
    return []


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _result(findings: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "verified": not findings,
        "findings": findings,
        "read_only": True,
        "advisory_only": True,
    }
    for gate in CLAIM_GATES:
        result[gate] = False
    return result


if __name__ == "__main__":
    raise SystemExit(main())
