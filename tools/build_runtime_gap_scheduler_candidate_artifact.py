#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Render no-authority scheduler-candidate previews from gap reports.

This tool consumes the read-only runtime gap detector report and emits a
path-free preview artifact for candidate intents that are ready to become
scheduler candidates later. It does not enqueue, run the scheduler, write the
bridge, or grant runtime authority.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import run_runtime_gap_detector_report as gap_report  # noqa: E402
from tools.future_scale_contract_safety import (  # noqa: E402
    validate_exact_false_fields,
    validate_scalar_safety,
)
from waggledance.core.autonomy_growth.low_risk_policy import (  # noqa: E402
    is_low_risk_family,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


ARTIFACT_VERSION = "wd.runtime_gap_scheduler_candidate_artifact.v1"
SCHEMA_VERSION = "runtime_gap_scheduler_candidate_artifact.v1"
MEASUREMENT_SCOPE = "local_read_only_scheduler_candidate_preview"
JSON_ARTIFACT_NAME = "runtime_gap_scheduler_candidate_artifact.json"
MARKDOWN_ARTIFACT_NAME = "runtime_gap_scheduler_candidate_artifact.md"
TOP_LEVEL_FALSE_FIELDS = (
    "claim_gate_satisfied",
    "claim_safe",
    "literal_future_claim_safe",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "required_runtime_evidence_present",
    "runtime_detector_record_called",
    "digest_signals_into_intents_called",
    "scheduler_enqueue_allowed",
    "scheduler_tick_allowed",
    "scheduler_tick_executed",
    "queue_writes_applied",
    "control_plane_writes_applied",
    "bridge_event_written",
    "gate_skip_allowed",
    "promotion_gate_skip_allowed",
    "adversarial_gate_skip_allowed",
    "canary_gate_skip_allowed",
    "fast_track_priority",
)
CANDIDATE_FALSE_FIELDS = (
    "scheduler_enqueue_allowed",
    "scheduler_tick_allowed",
    "bridge_event_written",
    "runtime_authority_granted",
    "gate_skip_allowed",
    "promotion_gate_skip_allowed",
    "adversarial_gate_skip_allowed",
    "canary_gate_skip_allowed",
    "fast_track_priority",
    "raw_signal_payload_included",
    "raw_query_exported",
)
NOT_CLAIMED = (
    "No runtime gap detector write path was called.",
    "No growth intent was inserted or enqueued.",
    "No scheduler tick or low-risk grower execution was performed.",
    "No bridge event was appended.",
    "No fast-track gate skip was granted.",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a no-authority scheduler-candidate preview from a runtime "
            "gap detector report or signal export."
        ),
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Required safety flag: do not call network services.",
    )
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="Required safety flag: keep deterministic grouping semantics.",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--report-json",
        type=Path,
        default=None,
        help="Existing runtime_gap_detector_report JSON artifact.",
    )
    source.add_argument(
        "--signals-json",
        type=Path,
        default=None,
        help=(
            "Optional operator-owned signal export. The input path and raw "
            "payloads are not recorded in the candidate artifact."
        ),
    )
    parser.add_argument(
        "--min-signals-per-intent",
        type=int,
        default=2,
        help="Minimum accepted low-risk signals when building a report.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Optional output directory for JSON and markdown artifacts.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for report construction.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.offline or not args.deterministic:
        print(
            "runtime gap scheduler candidate artifact requires "
            "--offline --deterministic",
            file=sys.stderr,
        )
        return 2
    try:
        report = _load_or_build_report(args)
        artifact = build_runtime_gap_scheduler_candidate_artifact(report)
    except ValueError as exc:
        print(
            f"runtime gap scheduler candidate artifact FAILED: {exc}",
            file=sys.stderr,
        )
        return 1

    markdown = render_markdown(artifact)
    if args.out_dir is not None:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / JSON_ARTIFACT_NAME).write_text(
            json.dumps(artifact, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        (args.out_dir / MARKDOWN_ARTIFACT_NAME).write_text(
            markdown,
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps(artifact, indent=2, sort_keys=True, allow_nan=False))
    else:
        print(markdown, end="")
    return 0


def build_runtime_gap_scheduler_candidate_artifact(
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a path-free scheduler-candidate preview artifact."""

    report_dict = _plain_json_object(report, "runtime gap detector report")
    report_errors = gap_report.validate_runtime_gap_detector_report(report_dict)
    if report_errors:
        raise ValueError("source report invalid: " + "; ".join(report_errors))

    source_report_digest = sha256_digest(report_dict)
    candidates = [
        _scheduler_candidate_for(
            candidate,
            source_report_digest=source_report_digest,
        )
        for candidate in report_dict["candidate_intents"]
        if candidate.get("ready_for_scheduler_candidate") is True
    ]
    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": report_dict["generated_at_utc"],
        "measurement_scope": MEASUREMENT_SCOPE,
        "source_report_digest": source_report_digest,
        "source_report_version": report_dict["report_version"],
        "source_report_schema_version": report_dict["schema_version"],
        "source_report_measurement_scope": report_dict["measurement_scope"],
        "source_git_sha": report_dict["git_sha"],
        "source_branch": report_dict["source_branch"],
        "input_source_kind": report_dict["input_source_kind"],
        "source_candidate_intent_count": report_dict["candidate_intent_count"],
        "source_scheduler_candidate_count": report_dict[
            "scheduler_candidate_count"
        ],
        "scheduler_candidate_count": len(candidates),
        "blocked_candidate_count": (
            int(report_dict["candidate_intent_count"]) - len(candidates)
        ),
        "scheduler_candidates": candidates,
        "artifact_path_free": True,
        "claim_gate_satisfied": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "required_runtime_evidence_present": False,
        "runtime_detector_record_called": False,
        "digest_signals_into_intents_called": False,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "scheduler_tick_executed": False,
        "queue_writes_applied": False,
        "control_plane_writes_applied": False,
        "bridge_event_written": False,
        "gate_skip_allowed": False,
        "promotion_gate_skip_allowed": False,
        "adversarial_gate_skip_allowed": False,
        "canary_gate_skip_allowed": False,
        "fast_track_priority": False,
        "no_cloud_api_calls": True,
        "no_model_pull_or_download": True,
        "not_claimed": list(NOT_CLAIMED),
    }
    errors = validate_runtime_gap_scheduler_candidate_artifact(artifact)
    if errors:
        raise ValueError("; ".join(errors))
    return artifact


def validate_runtime_gap_scheduler_candidate_artifact(
    artifact: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    artifact_dict = _plain_json_object_or_none(artifact)
    if artifact_dict is None:
        return ["artifact must be a JSON object"]

    errors.extend(validate_exact_false_fields(
        artifact_dict,
        TOP_LEVEL_FALSE_FIELDS,
    ))
    if artifact_dict.get("artifact_version") != ARTIFACT_VERSION:
        errors.append("artifact_version mismatch")
    if artifact_dict.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version mismatch")
    if artifact_dict.get("measurement_scope") != MEASUREMENT_SCOPE:
        errors.append("measurement_scope mismatch")
    if artifact_dict.get("source_report_version") != gap_report.REPORT_VERSION:
        errors.append("source_report_version mismatch")
    if artifact_dict.get("source_report_schema_version") != gap_report.SCHEMA_VERSION:
        errors.append("source_report_schema_version mismatch")
    if artifact_dict.get("source_report_measurement_scope") != (
        gap_report.MEASUREMENT_SCOPE
    ):
        errors.append("source_report_measurement_scope mismatch")
    if not _is_sha256_ref(artifact_dict.get("source_report_digest")):
        errors.append("source_report_digest must be sha256 ref")
    if artifact_dict.get("artifact_path_free") is not True:
        errors.append("artifact_path_free must be true")
    if artifact_dict.get("no_cloud_api_calls") is not True:
        errors.append("no_cloud_api_calls must be true")
    if artifact_dict.get("no_model_pull_or_download") is not True:
        errors.append("no_model_pull_or_download must be true")

    candidates = artifact_dict.get("scheduler_candidates")
    if not isinstance(candidates, list):
        errors.append("scheduler_candidates must be a list")
        candidates = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            errors.append(f"scheduler_candidates[{index}] must be an object")
            continue
        errors.extend(
            f"scheduler_candidates[{index}].{error}"
            for error in validate_exact_false_fields(
                candidate,
                CANDIDATE_FALSE_FIELDS,
            )
        )
        if candidate.get("schema_version") != (
            "runtime_gap_scheduler_candidate_preview.v1"
        ):
            errors.append(f"scheduler_candidates[{index}].schema_version mismatch")
        if candidate.get("candidate_kind") != "runtime_gap_signal_group":
            errors.append(f"scheduler_candidates[{index}].candidate_kind mismatch")
        if candidate.get("queue_priority") != "normal":
            errors.append(f"scheduler_candidates[{index}].queue_priority must be normal")
        if candidate.get("ready_for_scheduler_candidate") is not True:
            errors.append(
                f"scheduler_candidates[{index}].ready_for_scheduler_candidate "
                "must be true"
            )
        if candidate.get("blockers") != []:
            errors.append(f"scheduler_candidates[{index}] ready with blockers")
        if not is_low_risk_family(str(candidate.get("family_kind", ""))):
            errors.append(f"scheduler_candidates[{index}].family_kind not low-risk")
        if not _is_sha256_ref(candidate.get("source_report_digest")):
            errors.append(
                f"scheduler_candidates[{index}].source_report_digest invalid"
            )
        if not _is_sha256_ref(candidate.get("candidate_digest")):
            errors.append(f"scheduler_candidates[{index}].candidate_digest invalid")
        if not _is_digest_hex(candidate.get("spec_seed_digest")):
            errors.append(f"scheduler_candidates[{index}].spec_seed_digest invalid")
        for field in ("signal_count", "priority_weight"):
            value = candidate.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"scheduler_candidates[{index}].{field} must be int")

    if artifact_dict.get("scheduler_candidate_count") != len(candidates):
        errors.append("scheduler_candidate_count does not match candidates")
    source_count = artifact_dict.get("source_candidate_intent_count")
    blocked_count = artifact_dict.get("blocked_candidate_count")
    if not isinstance(source_count, int) or isinstance(source_count, bool):
        errors.append("source_candidate_intent_count must be int")
    elif source_count < len(candidates):
        errors.append("source_candidate_intent_count less than candidates")
    if not isinstance(blocked_count, int) or isinstance(blocked_count, bool):
        errors.append("blocked_candidate_count must be int")
    elif isinstance(source_count, int) and blocked_count != source_count - len(candidates):
        errors.append("blocked_candidate_count does not match source count")

    errors.extend(validate_scalar_safety(artifact_dict))
    return errors


def render_markdown(artifact: Mapping[str, Any]) -> str:
    return "\n".join([
        "# Runtime Gap Scheduler Candidate Artifact",
        "",
        f"- artifact_version: `{artifact['artifact_version']}`",
        f"- measurement_scope: `{artifact['measurement_scope']}`",
        f"- input_source_kind: `{artifact['input_source_kind']}`",
        f"- scheduler_candidate_count: `{artifact['scheduler_candidate_count']}`",
        f"- blocked_candidate_count: `{artifact['blocked_candidate_count']}`",
        "",
        "This is a read-only preview artifact. It does not enqueue growth "
        "intents, run the scheduler, append bridge events, or grant runtime "
        "authority.",
        "",
        "Claim gates remain false:",
        *[
            f"- {field}: `{artifact[field]}`"
            for field in TOP_LEVEL_FALSE_FIELDS
        ],
    ]) + "\n"


def _scheduler_candidate_for(
    candidate: Mapping[str, Any],
    *,
    source_report_digest: str,
) -> dict[str, Any]:
    basis = {
        "source_report_digest": source_report_digest,
        "intent_key": candidate["intent_key"],
        "spec_seed_digest": candidate["spec_seed_digest"],
    }
    candidate_digest = sha256_digest(basis)
    candidate_id = (
        "runtime_gap_scheduler_candidate:"
        + candidate_digest.removeprefix("sha256:")
    )
    return {
        "schema_version": "runtime_gap_scheduler_candidate_preview.v1",
        "candidate_id": candidate_id,
        "candidate_digest": candidate_digest,
        "candidate_kind": "runtime_gap_signal_group",
        "source_report_digest": source_report_digest,
        "intent_key": candidate["intent_key"],
        "family_kind": candidate["family_kind"],
        "cell_coord": candidate["cell_coord"],
        "intent_seed": candidate["intent_seed"],
        "signal_count": candidate["signal_count"],
        "total_weight": candidate["total_weight"],
        "priority_weight": candidate["priority_estimate"],
        "spec_seed_digest": candidate["spec_seed_digest"],
        "queue_priority": "normal",
        "ready_for_scheduler_candidate": True,
        "blockers": [],
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "bridge_event_written": False,
        "runtime_authority_granted": False,
        "gate_skip_allowed": False,
        "promotion_gate_skip_allowed": False,
        "adversarial_gate_skip_allowed": False,
        "canary_gate_skip_allowed": False,
        "fast_track_priority": False,
        "raw_signal_payload_included": False,
        "raw_query_exported": False,
    }


def _load_or_build_report(args: argparse.Namespace) -> dict[str, Any]:
    if args.report_json is not None:
        return _load_report_json(args.report_json)

    signals = (
        gap_report._load_signal_export(args.signals_json)  # noqa: SLF001
        if args.signals_json is not None
        else None
    )
    return gap_report.build_runtime_gap_detector_report(
        signal_fixtures=signals,
        input_source_kind=(
            "operator_owned_signal_export"
            if args.signals_json is not None
            else "deterministic_fixture"
        ),
        min_signals_per_intent=args.min_signals_per_intent,
        now_utc=gap_report._parse_utc(args.now) if args.now else None,  # noqa: SLF001
    )


def _load_report_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("report-json could not be read") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("report-json must be valid JSON") from exc
    return _plain_json_object(loaded, "report-json")


def _plain_json_object(value: Any, label: str) -> dict[str, Any]:
    result = _plain_json_object_or_none(value)
    if result is None:
        raise ValueError(f"{label} must be a JSON object")
    return result


def _plain_json_object_or_none(value: Any) -> dict[str, Any] | None:
    try:
        plain = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        return None
    if not isinstance(plain, dict):
        return None
    return plain


def _is_sha256_ref(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == len("sha256:") + 64
        and value.startswith("sha256:")
        and _is_digest_hex(value[len("sha256:"):])
    )


def _is_digest_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


if __name__ == "__main__":
    raise SystemExit(main())
