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
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "waggledance.release_soak.v1"

STATUS_PASS_FIELDS = {
    "ci_status": "pass",
    "profile_s_smoke": "pass",
    "security_privacy_gate": "pass",
    "axis_a_regression": "pass",
    "axis_b_gate": "pass",
    "release_notes_anti_claims": "pass",
}


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
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


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
) -> list[str]:
    blockers: list[str] = []

    if evidence.get("schema_version") != SCHEMA_VERSION:
        blockers.append("soak_evidence_schema_version_invalid")
    if evidence.get("target_version") != readiness.target_version:
        blockers.append("soak_evidence_target_version_mismatch")
    if not evidence.get("commit"):
        blockers.append("soak_evidence_commit_missing")
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
    if not isinstance(duration, (int, float)) or duration < required_hours:
        blockers.append(f"soak_evidence_duration_lt_{required_hours}h")
    if ended_at is not None and ended_at.date() < readiness.soak_end:
        blockers.append("soak_evidence_ended_before_required_soak_end")

    if evidence.get("silent_failures") != 0:
        blockers.append("soak_evidence_silent_failures_nonzero")
    if evidence.get("error_log_clean") is not True:
        blockers.append("soak_evidence_error_log_not_clean")
    if evidence.get("docker_stable_policy") != "finalized":
        blockers.append("soak_evidence_docker_policy_not_finalized")

    for field, expected in STATUS_PASS_FIELDS.items():
        if evidence.get(field) != expected:
            blockers.append(f"soak_evidence_{field}_not_{expected}")

    return blockers


def evaluate_release_gate(
    readiness_path: Path | str,
    *,
    soak_evidence_path: Path | str | None = None,
    today: dt.date | None = None,
) -> dict[str, Any]:
    today = today or dt.datetime.now(dt.UTC).date()
    blockers: list[str] = []
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
    else:
        try:
            evidence = json.loads(soak_evidence_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            blockers.append(f"soak_evidence_unreadable:{exc.__class__.__name__}")
        else:
            if not isinstance(evidence, dict):
                blockers.append("soak_evidence_not_object")
            else:
                blockers.extend(_validate_soak_evidence(evidence, readiness))

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
        "--allow-hold",
        action="store_true",
        help="Exit 0 even when the gate correctly reports a hold decision.",
    )
    args = parser.parse_args(argv)

    result = evaluate_release_gate(
        args.release_readiness,
        soak_evidence_path=args.soak_evidence,
        today=args.today,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["decision"] == "pass" or args.allow_hold:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
