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
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_release_gate import (
    COMMIT_PATTERN,
    SCHEMA_VERSION,
    STATUS_PASS_FIELDS,
    parse_release_readiness,
)
from tools.run_release_ci_status_evidence import (
    evaluate_report as evaluate_ci_status_report,
)
from tools.run_release_docker_policy_evidence import (
    evaluate_report as evaluate_docker_policy_report,
)


UNKNOWN_STATUS = "unknown"
BLOCKED_STATUS = "blocked"
LOCAL_ARTIFACT_COLLECTION_MODE = "local_artifacts"
MANUAL_COLLECTION_MODE = "manual"
LOCAL_ARTIFACT_DERIVED_FIELDS = (
    *STATUS_PASS_FIELDS,
    "silent_failures",
    "error_log_clean",
    "docker_stable_policy",
)

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
AXIS_B_TARGET_VERSION = "v3.12.0"
AXIS_B_EXPECTED_CELLS = {
    "bee_ops",
    "environment",
    "home_comfort",
    "hub",
    "logistics",
    "production",
    "safety_security",
}
AXIS_B_POSITIVE_CASES_PER_CELL = 15
AXIS_B_NEGATIVE_CASES_PER_CELL = 5
AXIS_B_QUALITY_FLOOR = 0.74
AXIS_B_MISMATCHED_BASELINE_QUALITY = 0.5
AXIS_B_MINIMUM_BASELINE_DELTA = 0.20
AXIS_B_PER_CELL_QUALITY_FLOOR = 0.6
CI_STATUS_EVIDENCE = "v3.12.0_ci_status.json"
DOCKER_POLICY_EVIDENCE = "v3.12.0_docker_policy.json"
SOAK_LOG_AUDIT = "v3.12.0_soak_log_audit.json"
SOAK_LOG_AUDIT_SCHEMA_VERSION = "waggledance.release_soak_log_audit.v1"
SOAK_LOG_AUDIT_COUNT_BLOCKERS = {
    "errors_detected",
    "silent_failures_detected",
    "undated_records_detected",
}
SOAK_LOG_REQUIRED_DEFAULT_SOURCES = {
    "docs/runs/error_log.jsonl",
    "docs/runs/release_soak_evidence/v3.12.0_history.jsonl",
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
    try:
        normalized = value.strip().replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.UTC)
        return parsed.astimezone(dt.UTC)
    except (ValueError, OverflowError) as exc:
        raise ValueError("invalid ISO-8601 timestamp") from exc


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
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        loaded = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
        )
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _exact_int(value: object) -> int | None:
    return value if type(value) is int else None


def _finite_number(value: object) -> int | float | None:
    if type(value) not in (int, float):
        return None
    try:
        return value if math.isfinite(float(value)) else None
    except OverflowError:
        return None


def _source_digest(path: Path) -> str | None:
    try:
        normalized = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError):
        return None
    digest = hashlib.sha256(normalized.encode("utf-8"))
    return "sha256:" + digest.hexdigest()


def _normalize_source_path(value: str) -> str:
    return value.replace("\\", "/").strip()


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
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        return None
    totals = metrics.get("_totals")
    if not isinstance(totals, dict):
        return None
    high = _exact_int(totals.get("SEVERITY.HIGH"))
    medium = _exact_int(totals.get("SEVERITY.MEDIUM"))
    if high is None or medium is None:
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

    descriptors = _exact_int(proof.get("synthetic_solver_descriptors_total"))
    lookups = _exact_int(proof.get("lookup_pass_count"))
    hits = _exact_int(proof.get("lookup_capability_hits_total"))
    fallback = _exact_int(proof.get("lookup_fifo_fallback_total"))
    misses = _exact_int(proof.get("lookup_miss_total"))
    warm_p99 = _finite_number(proof.get("lookup_p99_ms"))
    cold = proof.get("lookup_cold_after_attach")
    cold_p99 = (
        _finite_number(cold.get("lookup_p99_ms"))
        if isinstance(cold, dict)
        else None
    )
    if None in (
        descriptors,
        lookups,
        hits,
        fallback,
        misses,
        warm_p99,
        cold_p99,
    ):
        return UNKNOWN_STATUS

    stats = proof.get("hot_path_cache_stats")
    if not isinstance(stats, dict):
        return UNKNOWN_STATUS
    warm_hits = _exact_int(stats.get("warm_hits"))
    cold_hits = _exact_int(stats.get("cold_hits_warmed"))
    if warm_hits is None or cold_hits is None:
        return UNKNOWN_STATUS

    provider_jobs_delta = _exact_int(proof.get("provider_jobs_delta"))
    builder_jobs_delta = _exact_int(proof.get("builder_jobs_delta"))
    if provider_jobs_delta is None or builder_jobs_delta is None:
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
    if (
        warm_p99 < 0
        or warm_p99 > 1.0
        or cold_p99 < 0
        or cold_p99 > 50.0
    ):
        return BLOCKED_STATUS
    return "pass"


def _axis_b_hex_eval_status(evidence_root: Path) -> str:
    report = _read_json(evidence_root / AXIS_B_HEX_ALIGNED_EVAL)
    if report is None:
        return UNKNOWN_STATUS

    if report.get("schema_version") != "waggledance.axis_b_hex_eval.v1":
        return UNKNOWN_STATUS
    if report.get("target_version") != AXIS_B_TARGET_VERSION:
        return UNKNOWN_STATUS
    corpus = report.get("corpus")
    thresholds = report.get("thresholds")
    if not isinstance(corpus, dict) or not isinstance(thresholds, dict):
        return UNKNOWN_STATUS
    files = _exact_int(corpus.get("files"))
    total_positive = _exact_int(corpus.get("total_positive"))
    total_negative = _exact_int(corpus.get("total_negative"))
    quality = _finite_number(report.get("quality"))
    quality_floor = _finite_number(thresholds.get("quality_floor"))
    baseline = _finite_number(thresholds.get("mismatched_baseline_quality"))
    min_delta = _finite_number(thresholds.get("minimum_baseline_delta"))
    per_cell_floor = _finite_number(thresholds.get("per_cell_quality_floor"))
    micro_pos = _exact_int(report.get("micro_pos"))
    micro_pos_total = _exact_int(report.get("micro_pos_total"))
    micro_neg = _exact_int(report.get("micro_neg"))
    micro_neg_total = _exact_int(report.get("micro_neg_total"))
    if None in (
        files,
        total_positive,
        total_negative,
        quality,
        quality_floor,
        baseline,
        min_delta,
        per_cell_floor,
        micro_pos,
        micro_pos_total,
        micro_neg,
        micro_neg_total,
    ):
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
    if len(cells) != files or set(cells) != AXIS_B_EXPECTED_CELLS:
        return BLOCKED_STATUS
    if (
        quality_floor != AXIS_B_QUALITY_FLOOR
        or baseline != AXIS_B_MISMATCHED_BASELINE_QUALITY
        or min_delta != AXIS_B_MINIMUM_BASELINE_DELTA
        or per_cell_floor != AXIS_B_PER_CELL_QUALITY_FLOOR
    ):
        return BLOCKED_STATUS
    if not 0.0 <= quality <= 1.0:
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
    derived_file_scores: list[float] = []
    for row in per_file:
        if not isinstance(row, dict):
            return UNKNOWN_STATUS
        cell = row.get("cell")
        file_score = _finite_number(row.get("file_score"))
        pos_score = _finite_number(row.get("pos_score"))
        neg_score = _finite_number(row.get("neg_score"))
        pos_correct = _exact_int(row.get("pos_correct"))
        pos_total = _exact_int(row.get("pos_total"))
        neg_correct = _exact_int(row.get("neg_correct"))
        neg_total = _exact_int(row.get("neg_total"))
        if (
            not isinstance(cell, str)
            or file_score is None
            or pos_score is None
            or neg_score is None
            or pos_correct is None
            or pos_total is None
            or neg_correct is None
            or neg_total is None
        ):
            return UNKNOWN_STATUS
        seen_cells.add(cell)
        if (
            not 0.0 <= file_score <= 1.0
            or not 0.0 <= pos_score <= 1.0
            or not 0.0 <= neg_score <= 1.0
            or pos_total != AXIS_B_POSITIVE_CASES_PER_CELL
            or neg_total != AXIS_B_NEGATIVE_CASES_PER_CELL
            or pos_correct < 0
            or neg_correct < 0
            or pos_correct > pos_total
            or neg_correct > neg_total
        ):
            return BLOCKED_STATUS
        expected_pos_score = round(pos_correct / pos_total, 4)
        expected_neg_score = round(neg_correct / neg_total, 4)
        expected_file_score = round(
            (
                (pos_correct / pos_total)
                + (neg_correct / neg_total)
            )
            / 2,
            4,
        )
        if (
            pos_score != expected_pos_score
            or neg_score != expected_neg_score
            or file_score != expected_file_score
        ):
            return BLOCKED_STATUS
        derived_file_scores.append(expected_file_score)
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
    if quality != round(sum(derived_file_scores) / files, 4):
        return BLOCKED_STATUS
    if report.get("result") != "pass":
        return BLOCKED_STATUS
    return "pass"


def _ci_status(evidence_root: Path, expected_commit: str | None) -> str:
    report = _read_json(evidence_root / CI_STATUS_EVIDENCE)
    if report is None:
        return UNKNOWN_STATUS
    blockers = evaluate_ci_status_report(
        report,
        expected_commit=expected_commit if expected_commit else None,
    )
    return "pass" if not blockers else BLOCKED_STATUS


def _default_docker_evidence_root(evidence_root: Path) -> bool:
    normalized = evidence_root.as_posix().rstrip("/")
    default = DEFAULT_EVIDENCE_ROOT.as_posix()
    return normalized == default or normalized.endswith("/" + default)


def _docker_stable_policy(
    evidence_root: Path,
    expected_commit: str | None,
    *,
    source_root: Path,
) -> str:
    report = _read_json(evidence_root / DOCKER_POLICY_EVIDENCE)
    if report is not None:
        blockers = evaluate_docker_policy_report(
            report,
            expected_commit=expected_commit if expected_commit else None,
            source_root=source_root,
        )
        if not blockers:
            return "finalized"
    return "draft"


def _soak_log_audit_fields(
    evidence_root: Path,
    *,
    source_root: Path,
    expected_started_at: dt.datetime | None,
    expected_ended_at: dt.datetime | None,
) -> dict[str, Any]:
    fail_closed = {"silent_failures": None, "error_log_clean": False}
    if expected_started_at is None or expected_ended_at is None:
        return fail_closed
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
    normalized_sources = {_normalize_source_path(item) for item in source_files}
    if _default_docker_evidence_root(evidence_root) and not (
        SOAK_LOG_REQUIRED_DEFAULT_SOURCES <= normalized_sources
    ):
        return fail_closed
    source_hashes = report.get("source_hashes")
    if not isinstance(source_hashes, dict):
        return fail_closed
    for item in source_files:
        source = Path(item)
        if not source.is_absolute():
            source = source_root / source
        if not source.exists() or not source.is_file():
            return fail_closed
        expected_digest = source_hashes.get(item)
        if not isinstance(expected_digest, str):
            return fail_closed
        actual_digest = _source_digest(source)
        if actual_digest is None or actual_digest != expected_digest:
            return fail_closed
    if not isinstance(blockers, list):
        return fail_closed
    if not all(isinstance(item, str) for item in blockers):
        return fail_closed
    if any(item not in SOAK_LOG_AUDIT_COUNT_BLOCKERS for item in blockers):
        return fail_closed

    started_value = report.get("started_at_utc")
    ended_value = report.get("ended_at_utc")
    if not isinstance(started_value, str) or not isinstance(ended_value, str):
        return fail_closed
    try:
        started_at = _parse_timestamp(started_value)
        ended_at = _parse_timestamp(ended_value)
    except (TypeError, ValueError):
        return fail_closed
    silent_failures = _exact_int(report.get("silent_failure_count"))
    error_count = _exact_int(report.get("error_count"))
    undated_count = _exact_int(report.get("undated_record_count"))
    if (
        silent_failures is None
        or error_count is None
        or undated_count is None
    ):
        return fail_closed

    if (
        ended_at <= started_at
        or started_at > expected_started_at
        or ended_at < expected_ended_at
        or silent_failures < 0
        or error_count < 0
        or undated_count < 0
    ):
        return fail_closed

    audit_result = report.get("audit_result")
    error_log_clean = (
        audit_result == "pass"
        and silent_failures == 0
        and error_count == 0
        and undated_count == 0
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
    commit: str | None = None,
    soak_started_at_utc: dt.datetime | None = None,
    soak_ended_at_utc: dt.datetime | None = None,
    source_root: Path | str = ROOT,
) -> dict[str, Any]:
    """Derive release evidence fields from local artifacts without manual stubs."""

    source_root = Path(source_root)
    evidence_root = Path(evidence_root)
    release_notes = Path(release_notes)
    if not evidence_root.is_absolute():
        evidence_root = source_root / evidence_root
    if not release_notes.is_absolute():
        release_notes = source_root / release_notes
    return {
        "ci_status": _ci_status(evidence_root, commit),
        "docker_stable_policy": _docker_stable_policy(
            evidence_root,
            commit,
            source_root=source_root,
        ),
        "profile_s_smoke": _profile_s_smoke_status(evidence_root),
        "security_privacy_gate": _security_privacy_status(evidence_root),
        "axis_a_regression": _axis_a_solver_scale_status(evidence_root),
        "axis_b_gate": _axis_b_hex_eval_status(evidence_root),
        "release_notes_anti_claims": _release_notes_anti_claims_status(
            release_notes
        ),
        **_soak_log_audit_fields(
            evidence_root,
            source_root=source_root,
            expected_started_at=soak_started_at_utc,
            expected_ended_at=soak_ended_at_utc,
        ),
    }


def local_artifact_statuses(
    *,
    evidence_root: Path | str = DEFAULT_EVIDENCE_ROOT,
    release_notes: Path | str = DEFAULT_RELEASE_NOTES,
    commit: str | None = None,
    source_root: Path | str = ROOT,
) -> dict[str, str]:
    """Derive release statuses from local artifacts without manual stubs."""

    fields = local_artifact_evidence_fields(
        evidence_root=evidence_root,
        release_notes=release_notes,
        commit=commit,
        source_root=source_root,
    )
    return {
        field: value
        for field, value in fields.items()
        if field in STATUS_PASS_FIELDS and isinstance(value, str)
    }


def revalidate_local_artifact_evidence(
    evidence: dict[str, Any],
    *,
    source_root: Path | str = ROOT,
) -> dict[str, Any]:
    """Recompute every locally derived field from canonical artifacts."""

    if evidence.get("collection_mode") != LOCAL_ARTIFACT_COLLECTION_MODE:
        return {
            "verified": False,
            "reason": "collection_mode_invalid",
            "mismatches": [],
        }
    try:
        started_at = _parse_timestamp(evidence.get("started_at_utc"))
        ended_at = _parse_timestamp(evidence.get("ended_at_utc"))
    except (AttributeError, TypeError, ValueError):
        return {
            "verified": False,
            "reason": "soak_interval_invalid",
            "mismatches": [],
        }
    commit = evidence.get("commit")
    derived = local_artifact_evidence_fields(
        evidence_root=DEFAULT_EVIDENCE_ROOT,
        release_notes=DEFAULT_RELEASE_NOTES,
        commit=commit if isinstance(commit, str) else None,
        soak_started_at_utc=started_at,
        soak_ended_at_utc=ended_at,
        source_root=source_root,
    )
    mismatches = [
        field
        for field in LOCAL_ARTIFACT_DERIVED_FIELDS
        if (
            type(evidence.get(field)) is not type(derived.get(field))
            or evidence.get(field) != derived.get(field)
        )
    ]
    return {
        "verified": not mismatches,
        "reason": "verified" if not mismatches else "derived_fields_mismatch",
        "mismatches": mismatches,
        "derived_fields": {
            field: derived.get(field)
            for field in LOCAL_ARTIFACT_DERIVED_FIELDS
        },
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
    commit = evidence.get("commit")
    duration = _finite_number(evidence.get("duration_hours"))
    silent_failures = evidence.get("silent_failures")
    release_pass = (
        evidence.get("collection_mode") == LOCAL_ARTIFACT_COLLECTION_MODE
        and isinstance(commit, str)
        and COMMIT_PATTERN.fullmatch(commit) is not None
        and duration is not None
        and duration >= required_hours
        and type(silent_failures) is int
        and silent_failures == 0
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
    source_root: Path | str = ROOT,
) -> dict[str, Any]:
    """Build a soak evidence object in the release-gate schema."""

    readiness = _read_readiness(Path(release_readiness))
    started_at_utc = started_at_utc or _utc_midnight(readiness.soak_start)
    ended_at_utc = ended_at_utc or dt.datetime.now(dt.UTC)
    evidence_commit = commit if commit is not None else _current_commit()
    status_values = {field: UNKNOWN_STATUS for field in STATUS_PASS_FIELDS}

    for field, value in (status_overrides or {}).items():
        if field not in STATUS_PASS_FIELDS:
            raise ValueError(f"unknown release status field: {field}")
        status_values[field] = value
    if use_local_artifacts:
        local_fields = local_artifact_evidence_fields(
            evidence_root=evidence_root,
            release_notes=release_notes,
            commit=evidence_commit,
            soak_started_at_utc=started_at_utc,
            soak_ended_at_utc=ended_at_utc,
            source_root=source_root,
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
        if "docker_stable_policy" in local_fields:
            docker_stable_policy = str(local_fields["docker_stable_policy"])

    required_hours = (readiness.soak_end - readiness.soak_start).days * 24
    evidence: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "collection_mode": (
            LOCAL_ARTIFACT_COLLECTION_MODE
            if use_local_artifacts
            else MANUAL_COLLECTION_MODE
        ),
        "target_version": readiness.target_version,
        "commit": evidence_commit,
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
    parser.add_argument("--source-root", default=ROOT, type=Path)
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
            source_root=args.source_root,
        )
        write_soak_evidence(evidence, output=args.output, history=args.history)
    except (OSError, ValueError) as exc:
        print(f"collect_soak_evidence: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
