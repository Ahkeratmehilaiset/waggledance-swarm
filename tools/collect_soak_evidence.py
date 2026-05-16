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
        )
        write_soak_evidence(evidence, output=args.output, history=args.history)
    except (OSError, ValueError) as exc:
        print(f"collect_soak_evidence: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
