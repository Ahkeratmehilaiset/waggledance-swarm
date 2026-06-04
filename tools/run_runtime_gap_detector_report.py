#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only report over runtime gap detector-shaped signals.

The report is a planning artifact for the low-risk autogrowth lane. It mirrors
the public ``GapSignal`` allowlist/grouping semantics without calling
``RuntimeGapDetector.record()``, ``digest_signals_into_intents()``, an
``AutogrowthScheduler`` tick, or any control-plane write path.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.future_scale_contract_safety import (  # noqa: E402
    validate_exact_false_fields,
    validate_scalar_safety,
)
from waggledance.core.autonomy_growth.gap_intake import GapSignal  # noqa: E402
from waggledance.core.autonomy_growth.low_risk_policy import (  # noqa: E402
    LOW_RISK_FAMILY_KINDS,
    is_low_risk_family,
)


REPORT_VERSION = "wd.runtime_gap_detector_report.v1"
SCHEMA_VERSION = "runtime_gap_detector_report.v1"
JSON_ARTIFACT_NAME = "runtime_gap_detector_report.json"
MARKDOWN_ARTIFACT_NAME = "runtime_gap_detector_report.md"
MEASUREMENT_SCOPE = "local_read_only_gap_signal_report"
SOURCE_PATHS = (
    "waggledance/core/autonomy_growth/gap_intake.py",
    "waggledance/core/autonomy_growth/runtime_query_router.py",
    "waggledance/core/autonomy_growth/autogrowth_scheduler.py",
    "docs/architecture/WD_IMAGE1_FUNCTIONALITY_MANIFEST.md",
)
SAFE_FALSE_FIELDS = (
    "claim_gate_satisfied",
    "claim_safe",
    "literal_future_claim_safe",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "required_runtime_evidence_present",
    "runtime_detector_record_called",
    "digest_signals_into_intents_called",
    "scheduler_tick_executed",
    "queue_writes_applied",
    "control_plane_writes_applied",
)
NOT_CLAIMED = (
    "No runtime detector writes were performed.",
    "No growth intents were inserted or enqueued.",
    "No scheduler tick or low-risk grower execution was performed.",
    "No claim that fixture signals are production runtime evidence.",
)
DEFAULT_SIGNAL_FIXTURES: tuple[dict[str, Any], ...] = (
    {
        "kind": "runtime_miss",
        "family_kind": "threshold_rule",
        "cell_coord": "energy",
        "intent_seed": "hot_threshold",
        "weight": 1.0,
        "payload": {"miss_reason": "miss_no_solver", "feature_count": 2},
        "spec_seed": {
            "solver_name_seed": "hot_threshold",
            "spec": {
                "threshold": 30.0,
                "operator": ">",
                "true_label": "hot",
                "false_label": "cool",
            },
        },
    },
    {
        "kind": "runtime_miss",
        "family_kind": "threshold_rule",
        "cell_coord": "energy",
        "intent_seed": "hot_threshold",
        "weight": 1.0,
        "payload": {"miss_reason": "miss_no_solver", "feature_count": 2},
        "spec_seed": {
            "solver_name_seed": "hot_threshold",
            "spec": {
                "threshold": 31.0,
                "operator": ">",
                "true_label": "hot",
                "false_label": "cool",
            },
        },
    },
    {
        "kind": "runtime_miss",
        "family_kind": "threshold_rule",
        "cell_coord": "energy",
        "intent_seed": "hot_threshold",
        "weight": 1.0,
        "payload": {"miss_reason": "miss_no_solver", "feature_count": 2},
        "spec_seed": {
            "solver_name_seed": "hot_threshold",
            "spec": {
                "threshold": 32.0,
                "operator": ">",
                "true_label": "hot",
                "false_label": "cool",
            },
        },
    },
    {
        "kind": "runtime_miss",
        "family_kind": "lookup_table",
        "cell_coord": "general",
        "intent_seed": "color_map",
        "weight": 1.0,
        "payload": {"miss_reason": "miss_no_solver", "feature_count": 1},
        "spec_seed": {
            "solver_name_seed": "color_map",
            "spec": {"table": {"blue": "route"}, "default": "hold"},
        },
    },
    {
        "kind": "runtime_miss",
        "family_kind": "temporal_window_rule",
        "cell_coord": "safety",
        "intent_seed": "operator_window",
        "weight": 1.0,
    },
    {
        "kind": "runtime_miss",
        "family_kind": None,
        "cell_coord": "unknown",
        "intent_seed": "missing_family",
        "weight": 1.0,
    },
)
_SAFE_BRANCH_CHARS = re.compile(r"[^a-z0-9._/-]+")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a read-only runtime gap detector report.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Required safety flag: do not call network services.",
    )
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="Required safety flag: use deterministic grouping only.",
    )
    parser.add_argument(
        "--signals-json",
        type=Path,
        default=None,
        help=(
            "Optional signal export JSON. Accepts a list or an object with a "
            "'signals' list. The input path and raw payloads are not recorded."
        ),
    )
    parser.add_argument(
        "--min-signals-per-intent",
        type=int,
        default=2,
        help="Minimum accepted low-risk signals for a scheduler candidate.",
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
        help="Optional UTC timestamp override, e.g. 2026-06-04T09:10:00Z.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.offline or not args.deterministic:
        print(
            "runtime gap detector report requires --offline --deterministic",
            file=sys.stderr,
        )
        return 2
    try:
        signals = (
            _load_signal_export(args.signals_json)
            if args.signals_json is not None
            else DEFAULT_SIGNAL_FIXTURES
        )
        report = build_runtime_gap_detector_report(
            signal_fixtures=signals,
            input_source_kind=(
                "operator_owned_signal_export"
                if args.signals_json is not None
                else "deterministic_fixture"
            ),
            min_signals_per_intent=args.min_signals_per_intent,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except ValueError as exc:
        print(f"runtime gap detector report FAILED: {exc}", file=sys.stderr)
        return 1

    markdown = render_markdown(report)
    if args.out_dir is not None:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / JSON_ARTIFACT_NAME).write_text(
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        (args.out_dir / MARKDOWN_ARTIFACT_NAME).write_text(markdown, encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        print(markdown, end="")
    return 0


def build_runtime_gap_detector_report(
    *,
    signal_fixtures: Sequence[Mapping[str, Any] | GapSignal] | None = None,
    input_source_kind: str = "deterministic_fixture",
    min_signals_per_intent: int = 2,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    if min_signals_per_intent <= 0:
        raise ValueError("min_signals_per_intent must be positive")
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    raw_signals = tuple(
        DEFAULT_SIGNAL_FIXTURES if signal_fixtures is None else signal_fixtures
    )
    if not raw_signals:
        raise ValueError("signal_fixtures must be non-empty")

    accepted: list[GapSignal] = []
    input_rejections: Counter[str] = Counter()
    signal_rejections: Counter[str] = Counter()
    for index, raw in enumerate(raw_signals):
        signal, errors = _coerce_signal(raw)
        if errors:
            for error in errors:
                input_rejections[error] += 1
            continue
        assert signal is not None
        safety_errors = _input_safety_errors(signal)
        if safety_errors:
            input_rejections["unsafe_scalar"] += 1
            continue
        family = signal.family_kind
        if family is None or not str(family).strip():
            signal_rejections["missing_family_kind"] += 1
            continue
        if not is_low_risk_family(family):
            signal_rejections["family_not_low_risk"] += 1
            continue
        if not signal.kind:
            input_rejections["missing_kind"] += 1
            continue
        if not _is_finite_number(signal.weight) or signal.weight <= 0:
            input_rejections["invalid_weight"] += 1
            continue
        accepted.append(signal)
        _ = index

    candidate_intents = _candidate_intents(
        accepted,
        min_signals_per_intent=min_signals_per_intent,
    )
    ready_count = sum(
        1 for item in candidate_intents if item["ready_for_scheduler_candidate"]
    )
    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "git_sha": _git_text("rev-parse", "HEAD"),
        "source_branch": _source_branch_alias(),
        "measurement_scope": MEASUREMENT_SCOPE,
        "input_source_kind": input_source_kind,
        "source_paths": list(SOURCE_PATHS),
        "low_risk_family_allowlist": sorted(LOW_RISK_FAMILY_KINDS),
        "min_signals_per_intent": min_signals_per_intent,
        "input_signal_count": len(raw_signals),
        "accepted_low_risk_signal_count": len(accepted),
        "candidate_intent_count": len(candidate_intents),
        "scheduler_candidate_count": ready_count,
        "ready_for_scheduler_candidate": ready_count > 0,
        "candidate_intents": candidate_intents,
        "input_rejections": _counter_payload(input_rejections),
        "signal_rejections": _counter_payload(signal_rejections),
        "claim_gate_satisfied": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "required_runtime_evidence_present": False,
        "runtime_detector_record_called": False,
        "digest_signals_into_intents_called": False,
        "scheduler_tick_executed": False,
        "queue_writes_applied": False,
        "control_plane_writes_applied": False,
        "no_cloud_api_calls": True,
        "no_model_pull_or_download": True,
        "reproduce_command": (
            "python tools/run_runtime_gap_detector_report.py "
            "--offline --deterministic"
        ),
        "not_claimed": list(NOT_CLAIMED),
    }
    errors = validate_runtime_gap_detector_report(report)
    if errors:
        raise ValueError("; ".join(errors))
    return report


def validate_runtime_gap_detector_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    errors.extend(validate_exact_false_fields(report, SAFE_FALSE_FIELDS))
    if report.get("report_version") != REPORT_VERSION:
        errors.append("report_version mismatch")
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version mismatch")
    if report.get("measurement_scope") != MEASUREMENT_SCOPE:
        errors.append("measurement_scope mismatch")
    if report.get("no_cloud_api_calls") is not True:
        errors.append("no_cloud_api_calls must be true")
    if report.get("no_model_pull_or_download") is not True:
        errors.append("no_model_pull_or_download must be true")

    candidates = report.get("candidate_intents")
    if not isinstance(candidates, list):
        errors.append("candidate_intents must be a list")
        candidates = []
    ready_count = 0
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            errors.append(f"candidate_intents[{index}] must be an object")
            continue
        if not is_low_risk_family(str(candidate.get("family_kind", ""))):
            errors.append(f"candidate_intents[{index}].family_kind not low-risk")
        signal_count = candidate.get("signal_count")
        if not isinstance(signal_count, int) or isinstance(signal_count, bool):
            errors.append(f"candidate_intents[{index}].signal_count must be int")
        elif signal_count <= 0:
            errors.append(f"candidate_intents[{index}].signal_count must be positive")
        priority = candidate.get("priority_estimate")
        if not isinstance(priority, int) or isinstance(priority, bool):
            errors.append(f"candidate_intents[{index}].priority_estimate must be int")
        if candidate.get("ready_for_scheduler_candidate") is True:
            ready_count += 1
            if candidate.get("blockers") != []:
                errors.append(f"candidate_intents[{index}] ready with blockers")

    if report.get("candidate_intent_count") != len(candidates):
        errors.append("candidate_intent_count does not match candidate_intents")
    if report.get("scheduler_candidate_count") != ready_count:
        errors.append("scheduler_candidate_count does not match candidates")
    if report.get("ready_for_scheduler_candidate") is not (ready_count > 0):
        errors.append("ready_for_scheduler_candidate does not match candidates")

    for field in (
        "input_signal_count",
        "accepted_low_risk_signal_count",
        "candidate_intent_count",
        "scheduler_candidate_count",
        "min_signals_per_intent",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"{field} must be a non-negative int")

    errors.extend(
        validate_scalar_safety(
            report,
            allowed_metadata_path_values=SOURCE_PATHS,
        )
    )
    try:
        json.dumps(report, allow_nan=False)
    except ValueError as exc:
        errors.append(f"json serialization failed: {exc}")
    return errors


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Runtime Gap Detector Report",
        "",
        f"- report_version: `{report['report_version']}`",
        f"- measurement_scope: `{report['measurement_scope']}`",
        f"- input_source_kind: `{report['input_source_kind']}`",
        f"- input_signal_count: `{report['input_signal_count']}`",
        (
            "- accepted_low_risk_signal_count: "
            f"`{report['accepted_low_risk_signal_count']}`"
        ),
        f"- scheduler_candidate_count: `{report['scheduler_candidate_count']}`",
        "",
        "This is a read-only planning report. It does not call the runtime "
        "detector write path, enqueue growth intents, run the scheduler, or "
        "grant runtime authority.",
        "",
        "Claim gates remain false:",
    ]
    for field in SAFE_FALSE_FIELDS:
        lines.append(f"- {field}: `{report[field]}`")
    return "\n".join(lines) + "\n"


def _candidate_intents(
    signals: Sequence[GapSignal],
    *,
    min_signals_per_intent: int,
) -> list[dict[str, Any]]:
    by_key: dict[str, list[GapSignal]] = {}
    for signal in signals:
        assert signal.family_kind is not None
        key = _intent_key(signal.family_kind, signal.cell_coord, signal.intent_seed)
        by_key.setdefault(key, []).append(signal)

    candidates: list[dict[str, Any]] = []
    for key in sorted(by_key):
        grouped = by_key[key]
        first = grouped[0]
        latest_seed = next(
            (signal.spec_seed for signal in reversed(grouped) if signal.spec_seed),
            None,
        )
        total_weight = sum(float(signal.weight) for signal in grouped)
        priority_estimate = sum(int(float(signal.weight) * 10) for signal in grouped)
        blockers: list[str] = []
        if len(grouped) < min_signals_per_intent:
            blockers.append("below_min_signals")
        if latest_seed is None:
            blockers.append("missing_spec_seed")
        candidate = {
            "intent_key": key,
            "family_kind": first.family_kind,
            "cell_coord": first.cell_coord or "_",
            "intent_seed": first.intent_seed or "_default",
            "signal_count": len(grouped),
            "total_weight": _round(total_weight),
            "priority_estimate": priority_estimate,
            "has_spec_seed": latest_seed is not None,
            "spec_seed_digest": (
                _canonical_digest(latest_seed) if latest_seed is not None else None
            ),
            "ready_for_scheduler_candidate": not blockers,
            "blockers": blockers,
        }
        candidates.append(candidate)
    return candidates


def _coerce_signal(raw: Mapping[str, Any] | GapSignal) -> tuple[GapSignal | None, list[str]]:
    if isinstance(raw, GapSignal):
        return raw, []
    if not isinstance(raw, Mapping):
        return None, ["signal_not_object"]

    payload = raw.get("payload")
    signal_payload = raw.get("signal_payload")
    if payload is None and signal_payload is not None:
        if isinstance(signal_payload, str):
            try:
                loaded = json.loads(signal_payload)
            except json.JSONDecodeError:
                return None, ["signal_payload_invalid_json"]
            payload = loaded
        else:
            payload = signal_payload
    if payload is not None and not isinstance(payload, Mapping):
        return None, ["payload_not_object"]

    spec_seed = raw.get("spec_seed")
    if spec_seed is not None and not isinstance(spec_seed, Mapping):
        return None, ["spec_seed_not_object"]

    try:
        weight = float(raw.get("weight", 1.0))
    except (TypeError, ValueError):
        return None, ["invalid_weight"]

    return (
        GapSignal(
            kind=str(raw.get("kind") or ""),
            family_kind=_optional_str(raw.get("family_kind")),
            cell_coord=_optional_str(raw.get("cell_coord")),
            intent_seed=_optional_str(raw.get("intent_seed")),
            weight=weight,
            payload=dict(payload) if payload is not None else None,
            spec_seed=dict(spec_seed) if spec_seed is not None else None,
        ),
        [],
    )


def _input_safety_errors(signal: GapSignal) -> list[str]:
    payload = {
        "kind": signal.kind,
        "family_kind": signal.family_kind,
        "cell_coord": signal.cell_coord,
        "intent_seed": signal.intent_seed,
        "weight": signal.weight,
        "payload": signal.payload,
        "spec_seed": signal.spec_seed,
    }
    errors = validate_scalar_safety(payload)
    for field in ("kind", "family_kind", "cell_coord", "intent_seed"):
        value = getattr(signal, field)
        if isinstance(value, str) and len(value) > 96:
            errors.append(f"{field} exceeds 96 characters")
    if not _is_finite_number(signal.weight):
        errors.append("weight must be finite")
    return errors


def _load_signal_export(path: Path) -> Sequence[Mapping[str, Any]]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("signals-json could not be read") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("signals-json must be valid JSON") from exc
    if isinstance(loaded, Mapping):
        signals = loaded.get("signals")
    else:
        signals = loaded
    if not isinstance(signals, list):
        raise ValueError("signals-json must be a list or contain a signals list")
    if not all(isinstance(item, Mapping) for item in signals):
        raise ValueError("signals-json entries must be objects")
    return signals


def _intent_key(
    family_kind: str,
    cell_coord: str | None,
    intent_seed: str | None,
) -> str:
    cell = cell_coord or "_"
    seed = intent_seed or "_default"
    return f"{family_kind}:{cell}:{seed}"


def _counter_payload(counter: Counter[str]) -> dict[str, Any]:
    return {
        "total": sum(counter.values()),
        "by_reason": dict(sorted(counter.items())),
    }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _parse_utc(raw: str) -> datetime:
    if not raw.endswith("Z"):
        raise ValueError("--now must use Zulu UTC format")
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError("--now must match YYYY-MM-DDTHH:MM:SSZ") from exc


def _git_text(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        if args[:2] == ("rev-parse", "HEAD"):
            return "0" * 40
        if args[:2] == ("rev-parse", "--short=8"):
            return "000000"
        return "unknown"


def _source_branch_alias() -> str:
    branch = _git_text("branch", "--show-current").lower()
    alias = _SAFE_BRANCH_CHARS.sub("-", branch).strip(".-_/")
    if not alias or not alias[0].isalpha():
        alias = f"branch-{alias}" if alias else "branch-unknown"
    alias = alias[:80]
    probe = {"source_branch": alias}
    if validate_scalar_safety(probe):
        return "branch-redacted"
    return alias


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _round(value: float) -> float:
    if not math.isfinite(float(value)):
        raise ValueError("value must be finite")
    return round(float(value), 6)


if __name__ == "__main__":
    raise SystemExit(main())
