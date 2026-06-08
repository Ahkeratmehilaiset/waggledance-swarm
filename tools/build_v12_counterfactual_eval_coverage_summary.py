# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only V12 counterfactual-eval coverage summary."""
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

from tools.run_v12_a3_counterfactual_axis_proof import (  # noqa: E402
    build_a3_counterfactual_axis_proof,
)


REPORT_VERSION = "wd.v12.counterfactual_eval_coverage_summary.v0"
SOURCE_REPORT_VERSION = "wd.v12.a3_counterfactual_axis_proof.v0"
REQUIRED_GUARDRAILS = (
    "not_a_rival_benchmark",
    "does_not_claim_external_effect_execution",
    "does_not_apply_writes",
    "measures_one_local_domain_fixture",
    "measures_three_deterministic_variants",
    "runtime_smoke_is_not_axis_claim_upgrade",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a path-free, read-only coverage summary from the local "
            "V12 A3 counterfactual-evaluation proof."
        ),
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic output.",
    )
    parser.add_argument(
        "--min-variants",
        type=int,
        default=3,
        help="Minimum deterministic counterfactual variants expected.",
    )
    parser.add_argument(
        "--min-runtime-samples",
        type=int,
        default=20,
        help="Minimum runtime-condition smoke sample count expected.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_counterfactual_eval_coverage_summary(
            now_utc=_parse_utc(args.now) if args.now else None,
            min_variants=args.min_variants,
            min_runtime_samples=args.min_runtime_samples,
        )
    except ValueError as exc:
        print(
            f"counterfactual-eval coverage summary FAILED: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report["ok"] else 1


def build_counterfactual_eval_coverage_summary(
    *,
    now_utc: datetime | None = None,
    min_variants: int = 3,
    min_runtime_samples: int = 20,
    a3_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if min_variants < 1:
        raise ValueError("--min-variants must be >= 1")
    if min_runtime_samples < 1:
        raise ValueError("--min-runtime-samples must be >= 1")

    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at_utc = generated_at.isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    source = dict(
        a3_report
        if a3_report is not None
        else build_a3_counterfactual_axis_proof(now_utc=generated_at)
    )

    runtime_smoke = _mapping(source.get("runtime_condition_replay_smoke"))
    blockers = _source_blockers(source)
    coverage = _coverage(
        source,
        runtime_smoke=runtime_smoke,
        min_variants=min_variants,
        min_runtime_samples=min_runtime_samples,
    )
    blockers.extend(_coverage_blockers(coverage))
    blockers.extend(_runtime_smoke_blockers(runtime_smoke))

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": generated_at_utc,
        "ok": not blockers,
        "blockers": blockers,
        "source": _source_summary(source),
        "coverage": coverage,
        "next_eval_targets": _next_eval_targets(source, coverage),
        "authority_boundary": _authority_boundary(),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    coverage = _mapping(report.get("coverage"))
    runtime = _mapping(coverage.get("runtime_smoke"))
    authority = _mapping(report.get("authority_boundary"))
    lines = [
        "# V12 Counterfactual-Eval Coverage Summary",
        "",
        f"ok: `{_bool_text(report.get('ok'))}`",
        f"blockers: `{len(list(report.get('blockers') or []))}`",
        f"variants: `{coverage.get('variant_count', 0)}/"
        f"{coverage.get('min_variants', 0)}`",
        f"variants with gate delta: `{coverage.get('variants_with_gate_delta', 0)}`",
        f"runtime samples: `{runtime.get('sample_count', 0)}/"
        f"{runtime.get('min_samples', 0)}`",
        f"runtime status: `{runtime.get('observability_status', '')}`",
        "",
        "## Next Eval Targets",
    ]
    for target in list(report.get("next_eval_targets") or []):
        lines.append(f"- {target}")
    if not report.get("next_eval_targets"):
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Authority Boundary",
            f"runtime authority: `{_bool_text(authority.get('runtime_authority'))}`",
            f"promotion authority: `{_bool_text(authority.get('promotion_authority'))}`",
            f"scheduler authority: `{_bool_text(authority.get('scheduler_authority'))}`",
            f"bridge write authority: `{_bool_text(authority.get('bridge_write_authority'))}`",
            f"network authority: `{_bool_text(authority.get('network_authority'))}`",
            "",
            "This summary is read-only. It does not run live replay, promote "
            "solvers, enqueue schedulers, append bridge events, call network, "
            "or grant runtime authority.",
        ]
    )
    return "\n".join(lines) + "\n"


def _source_blockers(source: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if source.get("report_version") != SOURCE_REPORT_VERSION:
        blockers.append("source_report_version_mismatch")
    if source.get("axis_id") != "A3":
        blockers.append("source_axis_not_a3")
    if source.get("ok") is not True:
        blockers.append("source_ok_not_true")
    if source.get("counterfactual_delta_proven") is not True:
        blockers.append("counterfactual_delta_proven_not_true")
    if source.get("writes_applied") is not False:
        blockers.append("writes_applied_not_false")
    guardrails = _mapping(source.get("no_overclaim_guardrails"))
    for key in REQUIRED_GUARDRAILS:
        if guardrails.get(key) is not True:
            blockers.append(f"guardrail_{key}_not_true")
    return blockers


def _coverage(
    source: Mapping[str, Any],
    *,
    runtime_smoke: Mapping[str, Any],
    min_variants: int,
    min_runtime_samples: int,
) -> dict[str, Any]:
    variant_count = _as_int(source.get("variant_count"))
    runtime_samples = _as_int(runtime_smoke.get("sample_count"))
    return {
        "min_variants": min_variants,
        "variant_count": variant_count,
        "variants_with_kind_delta": _as_int(source.get("variants_with_kind_delta")),
        "variants_with_gate_delta": _as_int(source.get("variants_with_gate_delta")),
        "delta_field_count": _as_int(source.get("delta_field_count")),
        "delta_fields": [str(item) for item in list(source.get("delta_fields") or [])],
        "receipt_chain_verified": source.get("receipt_chain_verified") is True,
        "stored_consensus_replay_verified": (
            source.get("stored_consensus_replay_verified") is True
        ),
        "receipt_bound_stored_consensus_replay": (
            source.get("receipt_bound_stored_consensus_replay") is True
        ),
        "runtime_smoke": {
            "min_samples": min_runtime_samples,
            "sample_family": str(runtime_smoke.get("sample_family", "")),
            "sample_count": runtime_samples,
            "observability_status": str(runtime_smoke.get("observability_status", "")),
            "claim_label": str(runtime_smoke.get("claim_label", "")),
            "same_sample_set": runtime_smoke.get("same_sample_set") is True,
            "deterministic": runtime_smoke.get("deterministic") is True,
            "divergence_count": _as_int(runtime_smoke.get("divergence_count")),
            "no_delta": runtime_smoke.get("no_delta") is True,
            "delta_digest_present": runtime_smoke.get("delta_digest_present") is True,
            "privacy_canary_absent": runtime_smoke.get("privacy_canary_absent") is True,
            "payload_fields_exported": runtime_smoke.get("payload_fields_exported")
            is True,
            "raw_fields_exported": runtime_smoke.get("raw_fields_exported") is True,
            "runtime_authority_granted": (
                runtime_smoke.get("runtime_authority_granted") is True
            ),
            "external_writes_applied": (
                runtime_smoke.get("external_writes_applied") is True
            ),
        },
    }


def _coverage_blockers(coverage: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    runtime = _mapping(coverage.get("runtime_smoke"))
    if _as_int(coverage.get("variant_count")) < _as_int(coverage.get("min_variants")):
        blockers.append("variant_count_below_minimum")
    if _as_int(runtime.get("sample_count")) < _as_int(runtime.get("min_samples")):
        blockers.append("runtime_sample_count_below_minimum")
    if coverage.get("stored_consensus_replay_verified") is not True:
        blockers.append("stored_consensus_replay_verified_not_true")
    if "actual_gate" not in set(coverage.get("delta_fields") or []):
        blockers.append("actual_gate_delta_missing")
    if "kind" not in set(coverage.get("delta_fields") or []):
        blockers.append("kind_delta_missing")
    return blockers


def _runtime_smoke_blockers(runtime_smoke: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if runtime_smoke.get("ok") is not True:
        blockers.append("runtime_smoke_ok_not_true")
    if runtime_smoke.get("same_sample_set") is not True:
        blockers.append("runtime_smoke_same_sample_set_not_true")
    if runtime_smoke.get("deterministic") is not True:
        blockers.append("runtime_smoke_deterministic_not_true")
    if runtime_smoke.get("delta_digest_present") is not True:
        blockers.append("runtime_smoke_delta_digest_absent")
    if runtime_smoke.get("runtime_authority_granted") is not False:
        blockers.append("runtime_smoke_runtime_authority_not_false")
    if runtime_smoke.get("external_writes_applied") is not False:
        blockers.append("runtime_smoke_external_writes_not_false")
    if runtime_smoke.get("payload_fields_exported") is not False:
        blockers.append("runtime_smoke_payload_fields_exported_not_false")
    if runtime_smoke.get("raw_fields_exported") is not False:
        blockers.append("runtime_smoke_raw_fields_exported_not_false")
    if runtime_smoke.get("privacy_canary_absent") is not True:
        blockers.append("runtime_smoke_privacy_canary_absent_not_true")
    return blockers


def _source_summary(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "report_version": str(source.get("report_version", "")),
        "axis_id": str(source.get("axis_id", "")),
        "axis_name": str(source.get("axis_name", "")),
        "claim_label": str(source.get("claim_label", "")),
        "case_id": str(source.get("case_id", "")),
        "source_demo_version": str(source.get("source_demo_version", "")),
        "writes_applied": source.get("writes_applied") is True,
    }


def _next_eval_targets(
    source: Mapping[str, Any],
    coverage: Mapping[str, Any],
) -> list[str]:
    targets: list[str] = []
    runtime = _mapping(coverage.get("runtime_smoke"))
    if coverage.get("receipt_chain_verified") is not True:
        targets.append("bind the summary to a verified receipt bundle")
    if _as_int(coverage.get("variants_with_gate_delta")) < _as_int(
        coverage.get("variant_count")
    ):
        targets.append("add a gate-delta variant so every variant changes actual_gate")
    if _as_int(coverage.get("variant_count")) <= _as_int(coverage.get("min_variants")):
        targets.append("add a fourth deterministic variant outside the current trio")
    if str(runtime.get("sample_family")) == "scalar_unit_conversion_24_same_sample_set":
        targets.append("add a second runtime-condition sample family")
    if source.get("receipt_bound_stored_consensus_replay") is not True:
        targets.append("make stored-consensus replay receipt-bound in the default proof")
    return targets


def _authority_boundary() -> dict[str, Any]:
    return {
        "read_only_summary": True,
        "runtime_authority": False,
        "promotion_authority": False,
        "scheduler_authority": False,
        "bridge_write_authority": False,
        "network_authority": False,
        "storage_write_authority": False,
        "solver_execution_authority": False,
        "external_writes_applied": False,
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _bool_text(value: Any) -> str:
    return "true" if value is True else "false"


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("--now must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
