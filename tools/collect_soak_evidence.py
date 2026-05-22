#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Collect a fail-closed release soak evidence draft.

This tool does not decide release readiness. It writes evidence in the schema
validated by tools/check_release_gate.py and intentionally defaults incomplete
signals to a hold posture until the operator supplies explicit pass evidence.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_release_gate import (
    SCHEMA_VERSION,
    STATUS_PASS_FIELDS,
    parse_release_readiness,
)


UNKNOWN_STATUS = "unknown"
BLOCKED_STATUS = "blocked"

DEFAULT_EVIDENCE_ROOT = Path("docs/runs/release_soak_evidence")
DEFAULT_RELEASE_NOTES = Path("docs/releases/v3.12.0.md")

FINAL_BANDIT_REPORTS = (
    "v3.12.0_bandit_report_after_static_hardening_zero_medium.json",
    "v3.12.0_bandit_report.json",
)
FINAL_PIP_AUDIT_REPORTS = (
    "v3.12.0_pip_audit_report_lock_after_prune_osv.json",
    "v3.12.0_pip_audit_report_lock_after_prune.json",
    "v3.12.0_pip_audit_report_after_fixable_deps.json",
    "v3.12.0_pip_audit_report_after_direct_ci_deps.json",
    "v3.12.0_pip_audit_report.json",
)
PRIVACY_PRECHECK = "v3.12.0_security_privacy_precheck.md"
AXIS_A_SOLVER_SCALE_PROOF = (
    Path("v3.12.0_axis_a_solver_scale") / "solver_scale_proof.json"
)
AXIS_B_HEX_ALIGNED_EVAL = "v3.12.0_axis_b_hex_aligned_eval.json"
AXIS_B_EXPECTED_CELLS = {
    "bee_ops",
    "environment",
    "home_comfort",
    "hub",
    "logistics",
    "production",
    "safety_security",
}
AXIS_B_QUALITY_FLOOR = 0.74
AXIS_B_MISMATCHED_BASELINE_QUALITY = 0.5
AXIS_B_MINIMUM_BASELINE_DELTA = 0.20
AXIS_B_PER_CELL_QUALITY_FLOOR = 0.6
SOAK_LOG_AUDIT = "v3.12.0_soak_log_audit.json"
SOAK_LOG_AUDIT_SCHEMA_VERSION = "waggledance.release_soak_log_audit.v1"
SOAK_LOG_AUDIT_COUNT_BLOCKERS = {
    "errors_detected",
    "silent_failures_detected",
}
REQUIRED_RELEASE_NOTE_ANTI_CLAIMS = (
    "Does **not** claim AGI, consciousness, model superiority",
    "States Docker `:latest` will remain `v3.8.0`",
)
FORBIDDEN_RELEASE_NOTE_CLAIMS = (
    "beats all competitors",
    "branch isolation is solved",
    "3d topology ships",
    "per-cell sharding ships",
)


def _parse_timestamp(value: str) -> dt.datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _utc_midnight(value: dt.date) -> dt.datetime:
    return dt.datetime.combine(value, dt.time(), tzinfo=dt.UTC)


def _format_utc(value: dt.datetime) -> str:
    normalized = value.astimezone(dt.UTC).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _duration_hours(started_at: dt.datetime, ended_at: dt.datetime) -> int | float:
    hours = (ended_at - started_at).total_seconds() / 3600
    if hours.is_integer():
        return int(hours)
    return round(hours, 3)


def _current_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return completed.stdout.strip()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _first_existing(root: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def _bandit_high_medium_clean(report_path: Path) -> bool | None:
    report = _read_json(report_path)
    if report is None:
        return None
    totals = report.get("metrics", {}).get("_totals", {})
    if not isinstance(totals, dict):
        return None
    try:
        high = int(totals.get("SEVERITY.HIGH", 0))
        medium = int(totals.get("SEVERITY.MEDIUM", 0))
    except (TypeError, ValueError):
        return None
    return high == 0 and medium == 0


def _pip_audit_blocker_count(report_path: Path) -> int | None:
    report = _read_json(report_path)
    if report is None:
        return None
    dependencies = report.get("dependencies")
    if not isinstance(dependencies, list):
        return None
    count = 0
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            return None
        if "skip_reason" in dependency:
            count += 1
            continue
        vulns = dependency.get("vulns")
        if not isinstance(vulns, list):
            return None
        count += len(vulns)
    return count


def _privacy_precheck_ok(path: Path) -> bool | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if "74 passed" in text and "SMOKE_OK" in text:
        return True
    return False


def _security_privacy_status(evidence_root: Path) -> str:
    bandit_report = _first_existing(evidence_root, FINAL_BANDIT_REPORTS)
    pip_audit_report = _first_existing(evidence_root, FINAL_PIP_AUDIT_REPORTS)
    privacy_precheck = evidence_root / PRIVACY_PRECHECK
    partial_release_evidence = (
        bandit_report is not None
        or pip_audit_report is not None
        or privacy_precheck.exists()
    )

    if bandit_report is None or pip_audit_report is None:
        if partial_release_evidence:
            return BLOCKED_STATUS
        return UNKNOWN_STATUS

    bandit_clean = _bandit_high_medium_clean(bandit_report)
    pip_blockers = _pip_audit_blocker_count(pip_audit_report)
    privacy_ok = _privacy_precheck_ok(privacy_precheck)

    if bandit_clean is None or pip_blockers is None or privacy_ok is None:
        return BLOCKED_STATUS if partial_release_evidence else UNKNOWN_STATUS
    if not bandit_clean or pip_blockers > 0 or not privacy_ok:
        return BLOCKED_STATUS
    return "pass"


def _profile_s_smoke_status(evidence_root: Path) -> str:
    privacy_ok = _privacy_precheck_ok(evidence_root / PRIVACY_PRECHECK)
    if privacy_ok is None:
        return UNKNOWN_STATUS
    return "pass" if privacy_ok else BLOCKED_STATUS


def _release_notes_anti_claims_status(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return UNKNOWN_STATUS
    missing = [claim for claim in REQUIRED_RELEASE_NOTE_ANTI_CLAIMS if claim not in text]
    lowered = text.lower()
    forbidden = [claim for claim in FORBIDDEN_RELEASE_NOTE_CLAIMS if claim in lowered]
    if missing or forbidden:
        return BLOCKED_STATUS
    return "pass"


def _axis_a_solver_scale_status(evidence_root: Path) -> str:
    proof = _read_json(evidence_root / AXIS_A_SOLVER_SCALE_PROOF)
    if proof is None:
        return UNKNOWN_STATUS

    try:
        descriptors = int(proof.get("synthetic_solver_descriptors_total", 0))
        lookups = int(proof.get("lookup_pass_count", 0))
        hits = int(proof.get("lookup_capability_hits_total", -1))
        fallback = int(proof.get("lookup_fifo_fallback_total", -1))
        misses = int(proof.get("lookup_miss_total", -1))
        warm_p99 = float(proof.get("lookup_p99_ms"))
        cold = proof.get("lookup_cold_after_attach")
        cold_p99 = (
            float(cold.get("lookup_p99_ms")) if isinstance(cold, dict) else None
        )
    except (TypeError, ValueError):
        return UNKNOWN_STATUS

    stats = proof.get("hot_path_cache_stats")
    if not isinstance(stats, dict):
        return UNKNOWN_STATUS
    try:
        warm_hits = int(stats.get("warm_hits", -1))
        cold_hits = int(stats.get("cold_hits_warmed", -1))
    except (TypeError, ValueError):
        return UNKNOWN_STATUS

    try:
        provider_jobs_delta = int(proof.get("provider_jobs_delta", -1))
        builder_jobs_delta = int(proof.get("builder_jobs_delta", -1))
    except (TypeError, ValueError):
        return UNKNOWN_STATUS

    if (
        proof.get("production_hot_path_cache_attached") is not True
        or proof.get("lookup_benchmark_shape") != "hot_path_cache_attached_warm_pass"
        or proof.get("no_provider_credentials_required") is not True
        or proof.get("no_runtime_network_required") is not True
        or provider_jobs_delta != 0
        or builder_jobs_delta != 0
    ):
        return BLOCKED_STATUS
    if descriptors < 10_000 or lookups < 1_000:
        return BLOCKED_STATUS
    if hits != lookups or fallback != 0 or misses != 0:
        return BLOCKED_STATUS
    if warm_hits < lookups or cold_hits < lookups:
        return BLOCKED_STATUS
    if warm_p99 > 1.0 or cold_p99 is None or cold_p99 > 50.0:
        return BLOCKED_STATUS
    return "pass"


def _axis_b_hex_eval_status(evidence_root: Path) -> str:
    report = _read_json(evidence_root / AXIS_B_HEX_ALIGNED_EVAL)
    if report is None:
        return UNKNOWN_STATUS

    if report.get("schema_version") != "waggledance.axis_b_hex_eval.v1":
        return UNKNOWN_STATUS
    corpus = report.get("corpus")
    thresholds = report.get("thresholds")
    if not isinstance(corpus, dict) or not isinstance(thresholds, dict):
        return UNKNOWN_STATUS
    try:
        files = int(corpus.get("files", 0))
        total_positive = int(corpus.get("total_positive", 0))
        total_negative = int(corpus.get("total_negative", 0))
        quality = float(report.get("quality"))
        quality_floor = float(thresholds.get("quality_floor"))
        baseline = float(thresholds.get("mismatched_baseline_quality"))
        min_delta = float(thresholds.get("minimum_baseline_delta"))
        per_cell_floor = float(thresholds.get("per_cell_quality_floor"))
        micro_pos = int(report.get("micro_pos"))
        micro_pos_total = int(report.get("micro_pos_total"))
        micro_neg = int(report.get("micro_neg"))
        micro_neg_total = int(report.get("micro_neg_total"))
    except (TypeError, ValueError):
        return UNKNOWN_STATUS

    cells = corpus.get("cells")
    if not isinstance(cells, list):
        return BLOCKED_STATUS
    per_file = report.get("per_file")
    if not isinstance(per_file, list):
        return UNKNOWN_STATUS
    blockers = report.get("blockers")
    if not isinstance(blockers, list):
        return UNKNOWN_STATUS
    if blockers:
        return BLOCKED_STATUS
    if files != 7 or total_positive != 105 or total_negative != 35:
        return BLOCKED_STATUS
    if set(cells) != AXIS_B_EXPECTED_CELLS:
        return BLOCKED_STATUS
    if (
        quality_floor != AXIS_B_QUALITY_FLOOR
        or baseline != AXIS_B_MISMATCHED_BASELINE_QUALITY
        or min_delta != AXIS_B_MINIMUM_BASELINE_DELTA
        or per_cell_floor != AXIS_B_PER_CELL_QUALITY_FLOOR
    ):
        return BLOCKED_STATUS
    if (
        micro_pos_total != total_positive
        or micro_neg_total != total_negative
        or micro_neg != total_negative
    ):
        return BLOCKED_STATUS
    if len(per_file) != files:
        return BLOCKED_STATUS
    if (
        quality < AXIS_B_QUALITY_FLOOR
        or quality <= AXIS_B_MISMATCHED_BASELINE_QUALITY
        + AXIS_B_MINIMUM_BASELINE_DELTA
    ):
        return BLOCKED_STATUS
    seen_cells: set[str] = set()
    pos_correct_total = 0
    pos_total_seen = 0
    neg_correct_total = 0
    neg_total_seen = 0
    for row in per_file:
        if not isinstance(row, dict):
            return UNKNOWN_STATUS
        try:
            cell = str(row.get("cell"))
            file_score = float(row.get("file_score"))
            pos_correct = int(row.get("pos_correct"))
            pos_total = int(row.get("pos_total"))
            neg_correct = int(row.get("neg_correct"))
            neg_total = int(row.get("neg_total"))
        except (TypeError, ValueError):
            return UNKNOWN_STATUS
        seen_cells.add(cell)
        pos_correct_total += pos_correct
        pos_total_seen += pos_total
        neg_correct_total += neg_correct
        neg_total_seen += neg_total
        if file_score < AXIS_B_PER_CELL_QUALITY_FLOOR or neg_correct != neg_total:
            return BLOCKED_STATUS
    if seen_cells != AXIS_B_EXPECTED_CELLS:
        return BLOCKED_STATUS
    if (
        pos_correct_total != micro_pos
        or pos_total_seen != micro_pos_total
        or neg_correct_total != micro_neg
        or neg_total_seen != micro_neg_total
    ):
        return BLOCKED_STATUS
    if report.get("result") != "pass":
        return BLOCKED_STATUS
    return "pass"


def _soak_log_audit_fields(evidence_root: Path) -> dict[str, Any]:
    fail_closed = {"silent_failures": None, "error_log_clean": False}
    report = _read_json(evidence_root / SOAK_LOG_AUDIT)
    if report is None:
        return fail_closed
    if report.get("schema_version") != SOAK_LOG_AUDIT_SCHEMA_VERSION:
        return fail_closed
    if report.get("target_version") != "v3.12.0":
        return fail_closed

    source_files = report.get("source_files")
    blockers = report.get("blockers")
    if not isinstance(source_files, list) or not source_files:
        return fail_closed
    if not all(isinstance(item, str) and item.strip() for item in source_files):
        return fail_closed
    for item in source_files:
        source = Path(item)
        if not source.exists() or not source.is_file():
            return fail_closed
    if not isinstance(blockers, list):
        return fail_closed
    if not all(isinstance(item, str) for item in blockers):
        return fail_closed
    if any(item not in SOAK_LOG_AUDIT_COUNT_BLOCKERS for item in blockers):
        return fail_closed

    try:
        started_at = _parse_timestamp(str(report.get("started_at_utc", "")))
        ended_at = _parse_timestamp(str(report.get("ended_at_utc", "")))
        silent_failures = int(report.get("silent_failure_count"))
        error_count = int(report.get("error_count"))
    except (TypeError, ValueError):
        return fail_closed

    if ended_at <= started_at or silent_failures < 0 or error_count < 0:
        return fail_closed

    audit_result = report.get("audit_result")
    error_log_clean = (
        audit_result == "pass"
        and silent_failures == 0
        and error_count == 0
        and blockers == []
        and report.get("error_log_clean") is True
    )
    if audit_result not in {"pass", "blocked"}:
        return fail_closed
    if audit_result == "pass" and not error_log_clean:
        return fail_closed

    return {
        "silent_failures": silent_failures,
        "error_log_clean": error_log_clean,
    }


def local_artifact_evidence_fields(
    *,
    evidence_root: Path | str = DEFAULT_EVIDENCE_ROOT,
    release_notes: Path | str = DEFAULT_RELEASE_NOTES,
) -> dict[str, Any]:
    """Derive release evidence fields from local artifacts without manual stubs."""

    evidence_root = Path(evidence_root)
    release_notes = Path(release_notes)
    return {
        "profile_s_smoke": _profile_s_smoke_status(evidence_root),
        "security_privacy_gate": _security_privacy_status(evidence_root),
        "axis_a_regression": _axis_a_solver_scale_status(evidence_root),
        "axis_b_gate": _axis_b_hex_eval_status(evidence_root),
        "release_notes_anti_claims": _release_notes_anti_claims_status(
            release_notes
        ),
        **_soak_log_audit_fields(evidence_root),
    }


def local_artifact_statuses(
    *,
    evidence_root: Path | str = DEFAULT_EVIDENCE_ROOT,
    release_notes: Path | str = DEFAULT_RELEASE_NOTES,
) -> dict[str, str]:
    """Derive release statuses from local artifacts without manual stubs."""

    fields = local_artifact_evidence_fields(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )
    return {
        field: value
        for field, value in fields.items()
        if field in STATUS_PASS_FIELDS and isinstance(value, str)
    }


def _read_readiness(readiness_path: Path) -> Any:
    text = readiness_path.read_text(encoding="utf-8")
    readiness, blockers = parse_release_readiness(text)
    if readiness is None or blockers:
        raise ValueError(
            "release readiness could not be parsed: " + ", ".join(blockers)
        )
    return readiness


def _derive_result(evidence: dict[str, Any], required_hours: int) -> str:
    statuses_pass = all(
        evidence.get(field) == expected
        for field, expected in STATUS_PASS_FIELDS.items()
    )
    release_pass = (
        bool(evidence.get("commit"))
        and evidence.get("duration_hours", 0) >= required_hours
        and evidence.get("silent_failures") == 0
        and evidence.get("error_log_clean") is True
        and evidence.get("docker_stable_policy") == "finalized"
        and statuses_pass
    )
    return "pass" if release_pass else "hold"


def build_soak_evidence(
    release_readiness: Path | str,
    *,
    commit: str | None = None,
    started_at_utc: dt.datetime | None = None,
    ended_at_utc: dt.datetime | None = None,
    status_overrides: dict[str, str] | None = None,
    silent_failures: int | None = None,
    error_log_clean: bool = False,
    docker_stable_policy: str = "draft",
    use_local_artifacts: bool = False,
    evidence_root: Path | str = DEFAULT_EVIDENCE_ROOT,
    release_notes: Path | str = DEFAULT_RELEASE_NOTES,
) -> dict[str, Any]:
    """Build a soak evidence object in the release-gate schema."""

    readiness = _read_readiness(Path(release_readiness))
    started_at_utc = started_at_utc or _utc_midnight(readiness.soak_start)
    ended_at_utc = ended_at_utc or dt.datetime.now(dt.UTC)
    status_values = {field: UNKNOWN_STATUS for field in STATUS_PASS_FIELDS}

    for field, value in (status_overrides or {}).items():
        if field not in STATUS_PASS_FIELDS:
            raise ValueError(f"unknown release status field: {field}")
        status_values[field] = value
    if use_local_artifacts:
        local_fields = local_artifact_evidence_fields(
            evidence_root=evidence_root,
            release_notes=release_notes,
        )
        status_values.update({
            field: value
            for field, value in local_fields.items()
            if field in STATUS_PASS_FIELDS and isinstance(value, str)
        })
        if "silent_failures" in local_fields:
            silent_failures = local_fields["silent_failures"]
        if "error_log_clean" in local_fields:
            error_log_clean = bool(local_fields["error_log_clean"])

    required_hours = (readiness.soak_end - readiness.soak_start).days * 24
    evidence: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "target_version": readiness.target_version,
        "commit": commit if commit is not None else _current_commit(),
        "started_at_utc": _format_utc(started_at_utc),
        "ended_at_utc": _format_utc(ended_at_utc),
        "duration_hours": _duration_hours(started_at_utc, ended_at_utc),
        "silent_failures": silent_failures,
        "error_log_clean": error_log_clean,
        "docker_stable_policy": docker_stable_policy,
        **status_values,
    }
    evidence["result"] = _derive_result(evidence, required_hours)
    return evidence


def write_soak_evidence(
    evidence: dict[str, Any],
    *,
    output: Path | None = None,
    history: Path | None = None,
) -> None:
    encoded = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
    if history is not None:
        history.parent.mkdir(parents=True, exist_ok=True)
        with history.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(evidence, sort_keys=True) + "\n")


def _parse_status_override(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("status override must be FIELD=VALUE")
    field, status = value.split("=", 1)
    field = field.strip()
    status = status.strip()
    if field not in STATUS_PASS_FIELDS:
        allowed = ", ".join(sorted(STATUS_PASS_FIELDS))
        raise argparse.ArgumentTypeError(
            f"unknown status field {field!r}; expected one of: {allowed}"
        )
    if not status:
        raise argparse.ArgumentTypeError("status value must not be empty")
    return field, status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release-readiness",
        default="docs/release/RELEASE_READINESS.md",
        type=Path,
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--started-at-utc", type=_parse_timestamp)
    parser.add_argument("--ended-at-utc", type=_parse_timestamp)
    parser.add_argument(
        "--status",
        action="append",
        default=[],
        type=_parse_status_override,
        metavar="FIELD=VALUE",
    )
    parser.add_argument("--silent-failures", type=int, default=None)
    parser.add_argument("--error-log-clean", action="store_true")
    parser.add_argument("--docker-stable-policy", default="draft")
    parser.add_argument(
        "--use-local-artifacts",
        action="store_true",
        help=(
            "Derive supported status fields from local evidence artifacts. "
            "Local evidence overrides manual --status values for those fields."
        ),
    )
    parser.add_argument("--evidence-root", default=DEFAULT_EVIDENCE_ROOT, type=Path)
    parser.add_argument("--release-notes", default=DEFAULT_RELEASE_NOTES, type=Path)
    args = parser.parse_args(argv)

    status_overrides = dict(args.status)
    try:
        evidence = build_soak_evidence(
            args.release_readiness,
            commit=args.commit,
            started_at_utc=args.started_at_utc,
            ended_at_utc=args.ended_at_utc,
            status_overrides=status_overrides,
            silent_failures=args.silent_failures,
            error_log_clean=args.error_log_clean,
            docker_stable_policy=args.docker_stable_policy,
            use_local_artifacts=args.use_local_artifacts,
            evidence_root=args.evidence_root,
            release_notes=args.release_notes,
        )
        write_soak_evidence(evidence, output=args.output, history=args.history)
    except (OSError, ValueError) as exc:
        print(f"collect_soak_evidence: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
