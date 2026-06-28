#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Offline runtime-readiness dry-run for hex subdivision.

This harness composes the merged subdivision runtime evidence without granting
runtime authority. It runs the end-to-end proof chain and the executor
admission dry-run, then emits one gate-facing readiness report:

* the evidence chain composes;
* executor admission remains blocked on the operator cutover gate;
* runtime mutation authority, transport, routing influence, and executor
  invocation stay false; and
* a supplied cutover authorization is rejected by this dry-run.

It is intentionally offline. It writes artifacts only under the requested
output directory and never activates production runtime behavior.
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

from tools.run_hex_subdivision_runtime_executor_admission_proof import (  # noqa: E402
    build_hex_subdivision_runtime_executor_admission_proof,
)
from tools.run_hex_subdivision_runtime_pipeline_e2e_proof import (  # noqa: E402
    build_hex_subdivision_runtime_pipeline_e2e_proof,
)
from waggledance.core.hex_topology.subdivision_runtime_executor_admission import (  # noqa: E402
    SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_BLOCKER,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


REPORT_VERSION = "wd.hex_subdivision_runtime_readiness_dry_run.v0"
READINESS_STATUS = "runtime_ready_evidence_available_activation_blocked"

AUTHORITY_BOUNDARY = {
    "runtime_mutation_authority": False,
    "runtime_authority_granted": False,
    "production_activation": False,
    "operator_cutover": False,
    "runtime_executor_invocation": False,
    "runtime_topology_mutation": False,
    "routing_influence": False,
    "transport": False,
    "claim_safe_upgrade": False,
    "bridge_append_allowed": False,
    "scheduler_enqueue_allowed": False,
    "merge_allowed": False,
}

_FORBIDDEN_TRUE_FIELDS = {
    "runtime_mutation_authority",
    "runtime_authority_granted",
    "production_activation",
    "operator_cutover",
    "operator_approval",
    "live_runtime_execution_authorized",
    "live_runtime_commit_authorized",
    "runtime_topology_mutation_applied",
    "routing_influence_applied",
    "transport_performed",
    "claim_safe_upgrade",
    "runtime_commit_performed",
    "runtime_executor_invoked",
    "bridge_append_allowed",
    "scheduler_enqueue_allowed",
    "merge_allowed",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="New output directory for the dry-run. It must not already exist.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-06-28T00:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_hex_subdivision_runtime_readiness_dry_run(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(
            f"hex subdivision runtime-readiness dry-run FAILED: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(
            "hex subdivision runtime-readiness dry-run OK: "
            f"{report['report_path']}"
        )
    else:
        print(
            "hex subdivision runtime-readiness dry-run FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_hex_subdivision_runtime_readiness_dry_run(
    *,
    out_dir: Path,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    if not out_dir.parent.exists():
        raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")
    out_dir.mkdir()

    pipeline_report = build_hex_subdivision_runtime_pipeline_e2e_proof(
        out_dir=out_dir / "pipeline_e2e",
        now_utc=generated_at,
    )
    admission_report = build_hex_subdivision_runtime_executor_admission_proof(
        out_dir=out_dir / "executor_admission",
        now_utc=generated_at,
    )
    admission = _mapping_as_dict(
        admission_report.get("subdivision_runtime_executor_admission")
    )
    forbidden_true_paths = _forbidden_true_flag_paths(
        {
            "authority_boundary": AUTHORITY_BOUNDARY,
            "pipeline_e2e": pipeline_report,
            "executor_admission_proof": admission_report,
        }
    )
    activation_blockers = list(admission.get("admission_blockers") or [])
    proof_checks = {
        "pipeline_e2e_proof_ok": pipeline_report.get("ok") is True,
        "pipeline_e2e_checks_all_true": _checks_all_true(
            pipeline_report.get("proof_checks")
        ),
        "executor_admission_proof_ok": admission_report.get("ok") is True,
        "executor_admission_checks_all_true": _checks_all_true(
            admission_report.get("proof_checks")
        ),
        "executor_admission_remains_blocked": (
            admission.get("ready_for_runtime_executor_admission") is False
            and activation_blockers
            == [SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_BLOCKER]
        ),
        "dry_run_rejects_cutover_authorization": (
            _mapping_as_dict(admission_report.get("proof_checks")).get(
                "cutover_authorization_is_not_accepted_by_dry_run"
            )
            is True
        ),
        "runtime_authority_false_everywhere": forbidden_true_paths == [],
        "production_activation_not_ready": True,
        "operator_cutover_gate_required": (
            SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_BLOCKER
            in activation_blockers
        ),
    }
    blockers = [
        name for name, passed in proof_checks.items() if passed is not True
    ]
    ok = not blockers
    report_path = out_dir / "hex_subdivision_runtime_readiness_dry_run.json"
    core = {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": ok,
        "blockers": blockers,
        "readiness_status": READINESS_STATUS,
        "runtime_ready_evidence_available": ok,
        "production_activation_ready": False,
        "runtime_mutation_authority": False,
        "authority_boundary": dict(AUTHORITY_BOUNDARY),
        "activation_blockers": activation_blockers,
        "required_next_gate": admission.get("required_next_gate"),
        "proof_checks": proof_checks,
        "forbidden_true_flag_paths": forbidden_true_paths,
        "source_reports": {
            "pipeline_e2e": {
                "report_version": pipeline_report.get("report_version"),
                "ok": pipeline_report.get("ok") is True,
                "proof_path": pipeline_report.get("proof_path"),
                "execution_request_live_runtime_authorized": (
                    pipeline_report.get(
                        "execution_request_live_runtime_authorized"
                    )
                ),
            },
            "executor_admission": {
                "report_version": admission_report.get("report_version"),
                "ok": admission_report.get("ok") is True,
                "proof_path": admission_report.get("proof_path"),
                "admission_decision": admission.get("admission_decision"),
                "ready_for_runtime_executor_admission": admission.get(
                    "ready_for_runtime_executor_admission"
                ),
                "runtime_executor_invoked": admission.get(
                    "runtime_executor_invoked"
                ),
                "runtime_commit_performed": admission.get(
                    "runtime_commit_performed"
                ),
                "runtime_topology_mutation_applied": admission.get(
                    "runtime_topology_mutation_applied"
                ),
                "transport_performed": admission.get("transport_performed"),
            },
        },
    }
    report = {
        **core,
        "readiness_digest": sha256_digest(core),
        "report_path": str(report_path),
    }
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _checks_all_true(value: Any) -> bool:
    return isinstance(value, Mapping) and bool(value) and all(
        item is True for item in value.values()
    )


def _forbidden_true_flag_paths(value: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            child = f"{prefix}.{key_text}" if prefix else key_text
            if key_text in _FORBIDDEN_TRUE_FIELDS and item is True:
                paths.append(child)
            paths.extend(_forbidden_true_flag_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child = f"{prefix}[{index}]" if prefix else f"[{index}]"
            paths.extend(_forbidden_true_flag_paths(item, child))
    return paths


def _mapping_as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


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


if __name__ == "__main__":
    raise SystemExit(main())
