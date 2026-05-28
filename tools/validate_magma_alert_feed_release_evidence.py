# SPDX-License-Identifier: BUSL-1.1
"""Validate a MAGMA alert-feed release evidence package for reviewer use."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.package_magma_alert_feed_release_evidence import (  # noqa: E402
    FORBIDDEN_OUTPUT_MARKERS,
    MANUAL_GATE_CHECKS,
    PACKAGE_VERSION,
)


_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-json", required=True, type=Path)
    parser.add_argument(
        "--ops-json",
        default=None,
        type=Path,
        help="Optional local ops snapshot used only to verify package digest.",
    )
    parser.add_argument(
        "--metrics-scrape",
        default=None,
        type=Path,
        help="Optional local metrics scrape used only to verify package digest.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        package = json.loads(args.package_json.read_text(encoding="utf-8"))
    except OSError:
        report = _failure_report("package_json_unreadable")
    except json.JSONDecodeError:
        report = _failure_report("package_json_decode_error")
    else:
        try:
            ops_bytes = args.ops_json.read_bytes() if args.ops_json else None
            metrics_bytes = (
                args.metrics_scrape.read_bytes() if args.metrics_scrape else None
            )
        except OSError:
            report = _failure_report("digest_artifact_unreadable")
        else:
            report = validate_magma_alert_feed_release_evidence_package(
                package,
                ops_bytes=ops_bytes,
                metrics_bytes=metrics_bytes,
            )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print("MAGMA alert-feed release evidence package validation OK")
    else:
        print(
            "MAGMA alert-feed release evidence package validation FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def validate_magma_alert_feed_release_evidence_package(
    package: Mapping[str, Any],
    *,
    ops_bytes: bytes | None = None,
    metrics_bytes: bytes | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    if not isinstance(package, Mapping):
        return _failure_report("package must be a JSON object")

    if package.get("package_version") != PACKAGE_VERSION:
        blockers.append("package_version_mismatch")
    if package.get("ok") is not True:
        blockers.append("package_ok_not_true")

    release_ref = package.get("release_ref")
    commit_sha = package.get("commit_sha")
    ci_run_ref = package.get("ci_run_ref")
    if not _safe_ref(release_ref):
        blockers.append("release_ref_unsafe")
        release_ref = "invalid_ref"
    if not isinstance(commit_sha, str) or not _COMMIT_RE.match(commit_sha):
        blockers.append("commit_sha_invalid")
        commit_sha = "invalid_commit"
    if not _safe_ref(ci_run_ref):
        blockers.append("ci_run_ref_unsafe")
        ci_run_ref = "invalid_ref"

    operator = _mapping(package.get("operator_ownership"))
    if operator.get("manual_review_required") is not True:
        blockers.append("operator_manual_review_not_required")
    if operator.get("automatic_release_decision") is not False:
        blockers.append("operator_automatic_release_decision_not_false")

    manual_gate = _mapping(package.get("manual_gate"))
    if manual_gate.get("status") != "operator_review_required":
        blockers.append("manual_gate_status_not_operator_review_required")
    if manual_gate.get("manual_review_required") is not True:
        blockers.append("manual_gate_review_not_required")
    if manual_gate.get("automatic_release_decision") is not False:
        blockers.append("manual_gate_automatic_release_decision_not_false")
    blockers.extend(_manual_gate_blockers(manual_gate))

    blockers.extend(_artifact_blockers(_mapping(package.get("input_artifacts"))))
    digest_checks = _digest_checks(
        _mapping(package.get("input_artifacts")),
        ops_bytes=ops_bytes,
        metrics_bytes=metrics_bytes,
    )
    blockers.extend(
        f"{name}_digest_mismatch"
        for name, status in digest_checks.items()
        if status == "mismatch"
    )
    warnings.extend(
        f"{name}_digest_not_checked"
        for name, status in digest_checks.items()
        if status == "not_checked"
    )

    authority = _mapping(package.get("authority"))
    blockers.extend(_authority_blockers(authority))
    privacy = _mapping(package.get("privacy"))
    blockers.extend(_privacy_blockers(privacy))
    blockers.extend(_serialized_privacy_blockers(package))

    hold_reasons = manual_gate.get("current_sample_hold_reasons")
    hold_reason_count = len(hold_reasons) if isinstance(hold_reasons, list) else 0
    report = {
        "ok": not blockers,
        "package_version": package.get("package_version"),
        "release_ref": release_ref,
        "commit_sha": commit_sha,
        "ci_run_ref": ci_run_ref,
        "manual_review_required": True,
        "automatic_release_decision": False,
        "release_decision_made": False,
        "digest_checks": digest_checks,
        "current_sample_hold_reason_count": hold_reason_count,
        "runtime_controls_added": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }
    report_markers = _forbidden_output_markers(json.dumps(report, sort_keys=True))
    if report_markers:
        report["ok"] = False
        report["blockers"] = sorted(
            set(report["blockers"])
            | {f"validator_report_forbidden_marker:{marker}" for marker in report_markers}
        )
    return report


def _manual_gate_blockers(manual_gate: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    checks = manual_gate.get("checks")
    if not isinstance(checks, list):
        return ["manual_gate_checks_missing"]
    by_id = {
        check.get("id"): check
        for check in checks
        if isinstance(check, Mapping) and isinstance(check.get("id"), str)
    }
    for expected in MANUAL_GATE_CHECKS:
        check = by_id.get(expected["id"])
        if check is None:
            blockers.append(f"manual_check_missing:{expected['id']}")
            continue
        if check.get("sample_metric") != expected["sample_metric"]:
            blockers.append(f"manual_check_metric_mismatch:{expected['id']}")
        if check.get("manual_pass_condition") != expected["manual_pass_condition"]:
            blockers.append(f"manual_check_condition_mismatch:{expected['id']}")
        if check.get("automatic_pass_decision") is not False:
            blockers.append(f"manual_check_auto_decision_not_false:{expected['id']}")
    return blockers


def _artifact_blockers(artifacts: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    for artifact_id, raw_key in (
        ("ops_json", "raw_payload_included"),
        ("metrics_scrape", "raw_scrape_included"),
    ):
        artifact = _mapping(artifacts.get(artifact_id))
        if not isinstance(artifact.get("sha256"), str) or not artifact[
            "sha256"
        ].startswith("sha256:"):
            blockers.append(f"{artifact_id}_digest_missing")
        if artifact.get(raw_key) is not False:
            blockers.append(f"{artifact_id}_{raw_key}_not_false")
    return blockers


def _digest_checks(
    artifacts: Mapping[str, Any],
    *,
    ops_bytes: bytes | None,
    metrics_bytes: bytes | None,
) -> dict[str, str]:
    checks: dict[str, str] = {}
    for artifact_id, data in (
        ("ops_json", ops_bytes),
        ("metrics_scrape", metrics_bytes),
    ):
        if data is None:
            checks[artifact_id] = "not_checked"
            continue
        expected = _mapping(artifacts.get(artifact_id)).get("sha256")
        checks[artifact_id] = "match" if expected == _sha256_hex(data) else "mismatch"
    return checks


def _authority_blockers(authority: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    expected_false = (
        "runtime_controls_added",
        "configuration_writes_applied",
        "import_or_replay_performed",
        "auto_merge_or_promotion_performed",
    )
    for field in expected_false:
        if authority.get(field) is not False:
            blockers.append(f"authority_{field}_not_false")
    return blockers


def _privacy_blockers(privacy: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    expected_false = (
        "raw_ops_payload_included",
        "raw_metrics_scrape_included",
        "urls_recorded",
        "hosts_recorded",
        "headers_recorded",
        "filesystem_paths_recorded",
        "exception_text_recorded",
        "raw_alertmanager_labels_recorded",
        "payload_material_recorded",
    )
    for field in expected_false:
        if privacy.get(field) is not False:
            blockers.append(f"privacy_{field}_not_false")
    found = privacy.get("forbidden_tokens_found")
    if found not in ([], None):
        blockers.append("privacy_forbidden_tokens_found_not_empty")
    return blockers


def _serialized_privacy_blockers(package: Mapping[str, Any]) -> list[str]:
    markers = _forbidden_output_markers(json.dumps(package, sort_keys=True))
    return [f"package_forbidden_marker:{marker}" for marker in markers]


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "package_version": None,
        "release_ref": "invalid_ref",
        "commit_sha": "invalid_commit",
        "ci_run_ref": "invalid_ref",
        "manual_review_required": True,
        "automatic_release_decision": False,
        "release_decision_made": False,
        "digest_checks": {},
        "current_sample_hold_reason_count": 0,
        "runtime_controls_added": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "blockers": [f"read_or_parse_failed:{reason}"],
        "warnings": [],
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_ref(value: Any) -> bool:
    return isinstance(value, str) and bool(_SAFE_REF_RE.match(value))


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _forbidden_output_markers(text: str) -> list[str]:
    lower_text = text.lower()
    return sorted(
        marker
        for marker in FORBIDDEN_OUTPUT_MARKERS
        if marker.lower() in lower_text
    )


if __name__ == "__main__":
    raise SystemExit(main())
