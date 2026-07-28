#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed stable-release gate for soak evidence.

The gate is intentionally documentation-driven: release posture remains in
docs/release/RELEASE_READINESS.md, while this tool makes the high-risk parts
machine-checkable before any stable tag or Docker :latest promotion.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA_VERSION = "waggledance.release_soak.v1"
LOCAL_ARTIFACT_COLLECTION_MODE = "local_artifacts"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")

STATUS_PASS_FIELDS = {
    "ci_status": "pass",
    "profile_s_smoke": "pass",
    "security_privacy_gate": "pass",
    "axis_a_regression": "pass",
    "axis_b_gate": "pass",
    "release_notes_anti_claims": "pass",
}

_DIAGNOSTIC_STRING_VALUES = {
    "pass",
    "hold",
    "blocked",
    "unknown",
    "draft",
    "finalized",
    LOCAL_ARTIFACT_COLLECTION_MODE,
}


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


@dataclass(frozen=True)
class ReleaseReadiness:
    latest_stable: str
    target_version: str
    no_earlier_than: dt.date
    soak_start: dt.date
    soak_end: dt.date


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value.strip())


def _parse_timestamp(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.UTC)
        return parsed.astimezone(dt.UTC)
    except (ValueError, OverflowError):
        return None


def _parse_cli_timestamp(value: str) -> dt.datetime:
    parsed = _parse_timestamp(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(
            "timestamp must be a valid ISO-8601 UTC instant"
        )
    return parsed


def parse_release_readiness(text: str) -> tuple[ReleaseReadiness | None, list[str]]:
    blockers: list[str] = []

    latest_match = re.search(r"\*\*Latest stable release\*\*:\s*`([^`]+)`", text)
    target_match = re.search(
        r"\*\*Next stable target\*\*:\s*`([^`]+)`,\s*no earlier than\s*([0-9-]+)",
        text,
    )
    soak_match = re.search(
        r"R22\.5 soak window must complete:\s*target\s*([0-9-]+)\s*->\s*([0-9-]+)",
        text,
    )

    if latest_match is None:
        blockers.append("latest_stable_release_missing")
    if target_match is None:
        blockers.append("next_stable_target_missing")
    if soak_match is None:
        blockers.append("r22_5_soak_window_missing")
    if blockers:
        return None, blockers

    try:
        no_earlier_than = _parse_date(target_match.group(2))
        soak_start = _parse_date(soak_match.group(1))
        soak_end = _parse_date(soak_match.group(2))
    except ValueError as exc:
        return None, [f"release_readiness_date_parse_error:{exc}"]

    if soak_end < soak_start:
        blockers.append("soak_window_end_before_start")

    return (
        ReleaseReadiness(
            latest_stable=latest_match.group(1),
            target_version=target_match.group(1),
            no_earlier_than=no_earlier_than,
            soak_start=soak_start,
            soak_end=soak_end,
        ),
        blockers,
    )


def _validate_soak_evidence(
    evidence: dict[str, Any],
    readiness: ReleaseReadiness,
    *,
    checked_at_utc: dt.datetime,
    source_root: Path,
) -> list[str]:
    blockers: list[str] = []

    if evidence.get("schema_version") != SCHEMA_VERSION:
        blockers.append("soak_evidence_schema_version_invalid")
    if evidence.get("target_version") != readiness.target_version:
        blockers.append("soak_evidence_target_version_mismatch")
    if evidence.get("collection_mode") != LOCAL_ARTIFACT_COLLECTION_MODE:
        blockers.append("soak_evidence_collection_mode_invalid")
    else:
        revalidation = _revalidate_local_artifact_evidence(
            evidence,
            source_root=source_root,
        )
        if revalidation.get("verified") is not True:
            blockers.append("soak_evidence_local_artifacts_not_verified")
    commit = evidence.get("commit")
    if not commit:
        blockers.append("soak_evidence_commit_missing")
    elif (
        not isinstance(commit, str)
        or COMMIT_PATTERN.fullmatch(commit) is None
    ):
        blockers.append("soak_evidence_commit_invalid")
    if evidence.get("result") != "pass":
        blockers.append("soak_evidence_result_not_pass")

    started_at = _parse_timestamp(evidence.get("started_at_utc"))
    ended_at = _parse_timestamp(evidence.get("ended_at_utc"))
    if started_at is None:
        blockers.append("soak_evidence_started_at_invalid")
    if ended_at is None:
        blockers.append("soak_evidence_ended_at_invalid")
    if started_at is not None and ended_at is not None and ended_at <= started_at:
        blockers.append("soak_evidence_ended_before_started")

    required_hours = (readiness.soak_end - readiness.soak_start).days * 24
    duration = evidence.get("duration_hours")
    duration_is_finite = False
    if (
        isinstance(duration, (int, float))
        and not isinstance(duration, bool)
    ):
        try:
            duration_is_finite = math.isfinite(float(duration))
        except OverflowError:
            duration_is_finite = False
    if not duration_is_finite or duration < required_hours:
        blockers.append(f"soak_evidence_duration_lt_{required_hours}h")
    if started_at is not None and ended_at is not None:
        elapsed_hours = (ended_at - started_at).total_seconds() / 3600
        expected_duration: int | float = (
            int(elapsed_hours)
            if elapsed_hours.is_integer()
            else round(elapsed_hours, 3)
        )
        if (
            elapsed_hours < required_hours
            and duration_is_finite
            and duration >= required_hours
        ):
            blockers.append(
                f"soak_evidence_elapsed_duration_lt_{required_hours}h"
            )
        if duration_is_finite and duration != expected_duration:
            blockers.append("soak_evidence_duration_mismatch")
    required_start_utc = dt.datetime.combine(
        readiness.soak_start,
        dt.time(),
        tzinfo=dt.UTC,
    )
    required_end_utc = dt.datetime.combine(
        readiness.soak_end,
        dt.time(),
        tzinfo=dt.UTC,
    )
    if ended_at is not None and ended_at < required_end_utc:
        blockers.append("soak_evidence_ended_before_required_soak_end")
    if started_at is not None and started_at > required_start_utc:
        blockers.append("soak_evidence_started_after_required_soak_start")
    if ended_at is not None and ended_at > checked_at_utc:
        blockers.append("soak_evidence_ended_in_future")

    silent_failures = evidence.get("silent_failures")
    if type(silent_failures) is not int or silent_failures != 0:
        blockers.append("soak_evidence_silent_failures_nonzero")
    if evidence.get("error_log_clean") is not True:
        blockers.append("soak_evidence_error_log_not_clean")
    if evidence.get("docker_stable_policy") != "finalized":
        blockers.append("soak_evidence_docker_policy_not_finalized")

    for field, expected in STATUS_PASS_FIELDS.items():
        if evidence.get(field) != expected:
            blockers.append(f"soak_evidence_{field}_not_{expected}")

    return blockers


def _revalidate_local_artifact_evidence(
    evidence: dict[str, Any],
    *,
    source_root: Path,
) -> dict[str, Any]:
    try:
        from tools.collect_soak_evidence import (
            revalidate_local_artifact_evidence,
        )
    except (ImportError, AttributeError) as exc:
        return {
            "verified": False,
            "reason": f"revalidator_unavailable:{exc.__class__.__name__}",
        }
    return revalidate_local_artifact_evidence(
        evidence,
        source_root=source_root,
    )


def _diagnostic_string(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return "<redacted>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if not isinstance(value, str):
        return "<redacted>"
    normalized = value.strip().lower()
    if normalized in _DIAGNOSTIC_STRING_VALUES:
        return normalized
    if re.fullmatch(r"v[0-9]+(?:\.[0-9]+){1,2}", value.strip()):
        return value.strip()
    return "<redacted>"


def _soak_evidence_diagnostics(
    evidence: dict[str, Any] | None,
    readiness: ReleaseReadiness,
    *,
    provided: bool,
    readable: bool,
    is_object: bool,
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "provided": provided,
        "readable": readable,
        "object": is_object,
    }
    if evidence is None:
        return diagnostics

    started_at = _parse_timestamp(evidence.get("started_at_utc"))
    ended_at = _parse_timestamp(evidence.get("ended_at_utc"))
    required_hours = (readiness.soak_end - readiness.soak_start).days * 24

    diagnostics.update({
        "target_version": _diagnostic_string(evidence.get("target_version")),
        "expected_target_version": readiness.target_version,
        "collection_mode": _diagnostic_string(evidence.get("collection_mode")),
        "expected_collection_mode": LOCAL_ARTIFACT_COLLECTION_MODE,
        "result": _diagnostic_string(evidence.get("result")),
        "expected_result": "pass",
        "commit_present": bool(evidence.get("commit")),
        "started_at_valid": started_at is not None,
        "ended_at_valid": ended_at is not None,
        "ended_at_date": ended_at.date().isoformat() if ended_at else None,
        "required_soak_end": readiness.soak_end.isoformat(),
        "duration_hours": _diagnostic_string(evidence.get("duration_hours")),
        "required_duration_hours": required_hours,
        "silent_failures": _diagnostic_string(evidence.get("silent_failures")),
        "expected_silent_failures": 0,
        "error_log_clean": _diagnostic_string(evidence.get("error_log_clean")),
        "expected_error_log_clean": True,
        "docker_stable_policy": _diagnostic_string(
            evidence.get("docker_stable_policy")
        ),
        "expected_docker_stable_policy": "finalized",
        "status_fields": {
            field: {
                "actual": _diagnostic_string(evidence.get(field)),
                "expected": expected,
            }
            for field, expected in STATUS_PASS_FIELDS.items()
        },
    })
    return diagnostics


def evaluate_release_gate(
    readiness_path: Path | str,
    *,
    soak_evidence_path: Path | str | None = None,
    today: dt.date | None = None,
    checked_at_utc: dt.datetime | None = None,
    source_root: Path | str = ROOT,
) -> dict[str, Any]:
    actual_now_utc = dt.datetime.now(dt.UTC)
    checked_at_utc = checked_at_utc or actual_now_utc
    if checked_at_utc.tzinfo is None:
        checked_at_utc = checked_at_utc.replace(tzinfo=dt.UTC)
    else:
        checked_at_utc = checked_at_utc.astimezone(dt.UTC)
    today = today or checked_at_utc.date()
    source_root = Path(source_root)
    blockers: list[str] = []
    if checked_at_utc > actual_now_utc:
        blockers.append("checked_at_utc_in_future")
    readiness_path = Path(readiness_path)
    if soak_evidence_path is not None:
        soak_evidence_path = Path(soak_evidence_path)

    try:
        readiness_text = readiness_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "decision": "hold",
            "blockers": [
                f"release_readiness_unreadable:{exc.__class__.__name__}"
            ],
        }

    readiness, parse_blockers = parse_release_readiness(readiness_text)
    blockers.extend(parse_blockers)
    if readiness is None:
        return {"decision": "hold", "blockers": blockers}

    if today < readiness.no_earlier_than:
        blockers.append("before_no_earlier_than_date")
    if today < readiness.soak_end:
        blockers.append("soak_window_incomplete")

    if soak_evidence_path is None:
        blockers.append("soak_evidence_missing")
        soak_diagnostics: dict[str, Any] = {
            "provided": False,
            "readable": False,
            "object": False,
        }
    else:
        try:
            evidence = json.loads(
                soak_evidence_path.read_text(encoding="utf-8"),
                object_pairs_hook=_unique_json_object,
            )
        except (OSError, ValueError) as exc:
            blockers.append(f"soak_evidence_unreadable:{exc.__class__.__name__}")
            soak_diagnostics = {
                "provided": True,
                "readable": False,
                "object": False,
            }
        else:
            if not isinstance(evidence, dict):
                blockers.append("soak_evidence_not_object")
                soak_diagnostics = {
                    "provided": True,
                    "readable": True,
                    "object": False,
                }
            else:
                blockers.extend(
                    _validate_soak_evidence(
                        evidence,
                        readiness,
                        checked_at_utc=checked_at_utc,
                        source_root=source_root,
                    )
                )
                soak_diagnostics = _soak_evidence_diagnostics(
                    evidence,
                    readiness,
                    provided=True,
                    readable=True,
                    is_object=True,
                )

    return {
        "decision": "pass" if not blockers else "hold",
        "blockers": blockers,
        "target_version": readiness.target_version,
        "latest_stable": readiness.latest_stable,
        "no_earlier_than": readiness.no_earlier_than.isoformat(),
        "soak_window": {
            "start": readiness.soak_start.isoformat(),
            "end": readiness.soak_end.isoformat(),
            "required_hours": (readiness.soak_end - readiness.soak_start).days * 24,
        },
        "soak_evidence_diagnostics": soak_diagnostics,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release-readiness",
        default="docs/release/RELEASE_READINESS.md",
        type=Path,
    )
    parser.add_argument("--soak-evidence", type=Path)
    parser.add_argument(
        "--today",
        type=_parse_date,
        help="Override current UTC date, YYYY-MM-DD, for reproducible checks.",
    )
    parser.add_argument(
        "--checked-at-utc",
        type=_parse_cli_timestamp,
        help=(
            "Override the exact UTC evaluation instant. Eligibility dates "
            "derive from this instant unless --today is also supplied."
        ),
    )
    parser.add_argument(
        "--source-root",
        default=ROOT,
        type=Path,
        help="Repository root containing canonical local release artifacts.",
    )
    parser.add_argument(
        "--allow-hold",
        action="store_true",
        help="Exit 0 even when the gate correctly reports a hold decision.",
    )
    args = parser.parse_args(argv)

    result = evaluate_release_gate(
        args.release_readiness,
        soak_evidence_path=args.soak_evidence,
        today=args.today,
        checked_at_utc=args.checked_at_utc,
        source_root=args.source_root,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["decision"] == "pass" or args.allow_hold:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
