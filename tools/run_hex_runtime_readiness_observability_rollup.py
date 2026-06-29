#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only hex runtime-readiness observability roll-up.

The roll-up surfaces the merged runtime-readiness dry-run evidence as status
data only. It never grants runtime activation, scheduler enqueue authority,
bridge append authority, merge authority, or gate-bypass authority.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_hex_subdivision_runtime_readiness_dry_run import (  # noqa: E402
    READINESS_STATUS as DRY_RUN_READINESS_STATUS,
    REPORT_VERSION as DRY_RUN_REPORT_VERSION,
)
from waggledance.core.hex_topology.subdivision_runtime_executor_admission import (  # noqa: E402
    SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_BLOCKER,
)


REPORT_VERSION = "wd.hex_runtime_readiness_observability_rollup.v0"
OUTPUT_FILENAME = "hex_runtime_readiness_observability_rollup.json"
ROLLUP_STATUS = "runtime_ready_evidence_observed_activation_blocked"
FORBIDDEN_TRUE_FIELDS = {
    "bridge_append_allowed",
    "claim_safe_upgrade",
    "gate_bypass_allowed",
    "github_write_allowed",
    "live_runtime_commit_authorized",
    "live_runtime_execution_authorized",
    "merge_allowed",
    "operator_approval",
    "operator_cutover",
    "production_activation",
    "production_activation_ready",
    "queue_write_allowed",
    "routing_influence",
    "routing_influence_applied",
    "runtime_activation_allowed",
    "runtime_authority_granted",
    "runtime_commit_performed",
    "runtime_executor_invocation",
    "runtime_executor_invoked",
    "runtime_mutation_authority",
    "runtime_topology_mutation",
    "runtime_topology_mutation_applied",
    "scheduler_enqueue_allowed",
    "scheduler_tick_allowed",
    "transport",
    "transport_performed",
}
REQUIRED_FALSE_TOP_LEVEL = (
    "production_activation_ready",
    "runtime_mutation_authority",
)
REQUIRED_FALSE_AUTHORITY = (
    "runtime_mutation_authority",
    "runtime_authority_granted",
    "production_activation",
    "operator_cutover",
    "runtime_executor_invocation",
    "runtime_topology_mutation",
    "routing_influence",
    "transport",
    "claim_safe_upgrade",
    "bridge_append_allowed",
    "scheduler_enqueue_allowed",
    "merge_allowed",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--readiness-report",
        type=Path,
        required=True,
        help="Path to hex_subdivision_runtime_readiness_dry_run.json.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Optional new output directory for the roll-up JSON. It must not "
            "already exist."
        ),
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-06-29T00:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_hex_runtime_readiness_observability_rollup(
            readiness_report_path=args.readiness_report,
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(
            f"hex runtime-readiness observability roll-up FAILED: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print("hex runtime-readiness observability roll-up OK")
    else:
        print(
            "hex runtime-readiness observability roll-up FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_hex_runtime_readiness_observability_rollup(
    *,
    readiness_report_path: Path,
    out_dir: Path | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    readiness = _read_json_object(readiness_report_path)
    forbidden_true_paths = _forbidden_true_flag_paths(readiness)
    source_reports = _mapping(readiness.get("source_reports"))
    pipeline = _mapping(source_reports.get("pipeline_e2e"))
    admission = _mapping(source_reports.get("executor_admission"))
    authority = _mapping(readiness.get("authority_boundary"))
    proof_checks = _mapping(readiness.get("proof_checks"))

    blockers = _blockers(
        readiness=readiness,
        authority=authority,
        proof_checks=proof_checks,
        pipeline=pipeline,
        admission=admission,
        forbidden_true_paths=forbidden_true_paths,
    )
    ok = not blockers
    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": ok,
        "blockers": blockers,
        "rollup_status": ROLLUP_STATUS if ok else "runtime_readiness_rollup_blocked",
        "runtime_ready_evidence_available": (
            ok and readiness.get("runtime_ready_evidence_available") is True
        ),
        "production_activation_ready": False,
        "runtime_mutation_authority": False,
        "gate_bypass_allowed": False,
        "source": {
            "readiness_report_version": _string(readiness.get("report_version")),
            "readiness_status": _string(readiness.get("readiness_status")),
            "readiness_ok": readiness.get("ok") is True,
            "readiness_digest": _string(readiness.get("readiness_digest")),
            "source_redacted": True,
            "path_free": True,
            "payloads_redacted": True,
        },
        "evidence": {
            "activation_blockers": list(readiness.get("activation_blockers") or []),
            "required_next_gate": _string(readiness.get("required_next_gate")),
            "pipeline_execution_request_digest": _string(
                pipeline.get("execution_request_digest")
            ),
            "executor_admission_execution_request_digest": _string(
                admission.get("runtime_execution_request_digest")
            ),
            "pipeline_execution_request_live_runtime_authorized": (
                pipeline.get("execution_request_live_runtime_authorized") is True
            ),
            "ready_for_runtime_executor_admission": (
                admission.get("ready_for_runtime_executor_admission") is True
            ),
            "runtime_executor_invoked": (
                admission.get("runtime_executor_invoked") is True
            ),
            "runtime_commit_performed": (
                admission.get("runtime_commit_performed") is True
            ),
            "runtime_topology_mutation_applied": (
                admission.get("runtime_topology_mutation_applied") is True
            ),
            "transport_performed": admission.get("transport_performed") is True,
        },
        "proof_checks": {
            "readiness_report_ok": readiness.get("ok") is True,
            "readiness_status_activation_blocked": (
                readiness.get("readiness_status") == DRY_RUN_READINESS_STATUS
            ),
            "runtime_ready_evidence_available": (
                readiness.get("runtime_ready_evidence_available") is True
            ),
            "production_activation_not_ready": (
                readiness.get("production_activation_ready") is False
            ),
            "runtime_mutation_authority_false": (
                readiness.get("runtime_mutation_authority") is False
            ),
            "authority_boundary_false": _required_false_values(
                authority,
                REQUIRED_FALSE_AUTHORITY,
            ),
            "operator_cutover_gate_required": (
                readiness.get("activation_blockers")
                == [SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_BLOCKER]
            ),
            "pipeline_executor_digest_matches": (
                isinstance(pipeline.get("execution_request_digest"), str)
                and pipeline.get("execution_request_digest")
                == admission.get("runtime_execution_request_digest")
            ),
            "executor_admission_blocked": (
                admission.get("ready_for_runtime_executor_admission") is False
            ),
            "no_runtime_executor_invoked": (
                admission.get("runtime_executor_invoked") is False
            ),
            "no_runtime_commit_performed": (
                admission.get("runtime_commit_performed") is False
            ),
            "no_runtime_topology_mutation_applied": (
                admission.get("runtime_topology_mutation_applied") is False
            ),
            "no_transport_performed": admission.get("transport_performed") is False,
            "dry_run_proof_runtime_authority_false_everywhere": (
                proof_checks.get("runtime_authority_false_everywhere") is True
            ),
            "rollup_forbidden_true_flags_absent": forbidden_true_paths == [],
        },
        "forbidden_true_flag_paths": forbidden_true_paths,
        "authority_boundary": _authority_boundary(),
    }
    if out_dir is not None:
        out_dir = out_dir.resolve()
        if out_dir.exists():
            raise ValueError(f"out_dir must not exist: {out_dir}")
        if not out_dir.parent.exists():
            raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")
        out_dir.mkdir()
        report_path = out_dir / OUTPUT_FILENAME
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def _blockers(
    *,
    readiness: Mapping[str, Any],
    authority: Mapping[str, Any],
    proof_checks: Mapping[str, Any],
    pipeline: Mapping[str, Any],
    admission: Mapping[str, Any],
    forbidden_true_paths: Sequence[str],
) -> list[str]:
    blockers: list[str] = []
    if readiness.get("report_version") != DRY_RUN_REPORT_VERSION:
        blockers.append("readiness_report_version_unexpected")
    if readiness.get("ok") is not True:
        blockers.append("readiness_report_not_ok")
    if readiness.get("readiness_status") != DRY_RUN_READINESS_STATUS:
        blockers.append("readiness_status_not_activation_blocked")
    if readiness.get("runtime_ready_evidence_available") is not True:
        blockers.append("runtime_ready_evidence_not_available")
    blockers.extend(
        f"{field}_not_false"
        for field in REQUIRED_FALSE_TOP_LEVEL
        if readiness.get(field) is not False
    )
    blockers.extend(
        f"authority_boundary.{field}_not_false"
        for field in REQUIRED_FALSE_AUTHORITY
        if authority.get(field) is not False
    )
    if readiness.get("activation_blockers") != [
        SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_BLOCKER
    ]:
        blockers.append("operator_cutover_activation_blocker_missing")
    if not (
        isinstance(pipeline.get("execution_request_digest"), str)
        and pipeline.get("execution_request_digest")
        == admission.get("runtime_execution_request_digest")
    ):
        blockers.append("pipeline_executor_digest_mismatch")
    if pipeline.get("execution_request_live_runtime_authorized") is not False:
        blockers.append("pipeline_live_runtime_authorized_not_false")
    for field in (
        "ready_for_runtime_executor_admission",
        "runtime_executor_invoked",
        "runtime_commit_performed",
        "runtime_topology_mutation_applied",
        "transport_performed",
    ):
        if admission.get(field) is not False:
            blockers.append(f"executor_admission.{field}_not_false")
    if proof_checks.get("runtime_authority_false_everywhere") is not True:
        blockers.append("dry_run_runtime_authority_false_everywhere_not_true")
    blockers.extend(
        f"forbidden_true_flag:{path}" for path in forbidden_true_paths
    )
    return sorted(dict.fromkeys(blockers))


def _read_json_object(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise ValueError(f"readiness report missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"readiness report is not valid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("readiness report must be a JSON object")
    return payload


def _forbidden_true_flag_paths(value: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            child = f"{prefix}.{key_text}" if prefix else key_text
            if key_text in FORBIDDEN_TRUE_FIELDS and item is True:
                paths.append(child)
            paths.extend(_forbidden_true_flag_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child = f"{prefix}[{index}]" if prefix else f"[{index}]"
            paths.extend(_forbidden_true_flag_paths(item, child))
    return paths


def _required_false_values(value: Mapping[str, Any], keys: Sequence[str]) -> bool:
    return all(value.get(key) is False for key in keys)


def _authority_boundary() -> dict[str, bool]:
    return {
        "read_only_report": True,
        "bridge_append_allowed": False,
        "queue_write_allowed": False,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "github_write_allowed": False,
        "runtime_activation_allowed": False,
        "runtime_mutation_authority": False,
        "gate_bypass_allowed": False,
        "merge_allowed": False,
        "network_required": False,
        "payload_export_allowed": False,
    }


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise ValueError(f"--now requires a UTC timestamp: {value}")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


if __name__ == "__main__":
    raise SystemExit(main())
