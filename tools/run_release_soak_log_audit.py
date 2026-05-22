#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write v3.12 soak log audit evidence from explicit source files."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "waggledance.release_soak_log_audit.v1"
DEFAULT_OUTPUT = (
    Path("docs")
    / "runs"
    / "release_soak_evidence"
    / "v3.12.0_soak_log_audit.json"
)
DEFAULT_TARGET_VERSION = "v3.12.0"
ERROR_PATTERN = re.compile(
    r"\b(errors?|failed|failures?|exceptions?|tracebacks?|fatal)\b",
    re.I,
)
SILENT_FAILURE_PATTERN = re.compile(r"\bsilent[-_ ]failure(s)?\b", re.I)
BENIGN_ERROR_PHRASES = (
    "0 errors",
    "zero errors",
    "no errors",
    "0 failed",
    "zero failed",
    "no failed",
    "0 failures",
    "zero failures",
    "no failures",
    "no tracebacks",
)
BENIGN_SILENT_FAILURE_PHRASES = (
    "no silent failures",
    "0 silent failures",
    "zero silent failures",
)


def _int_count(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, list):
        return len(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
        return int(bool(stripped))
    return 0


def _scan_text(text: str) -> tuple[int, int]:
    silent_failures = 0
    errors = 0
    for line in text.splitlines():
        lowered = line.lower()
        if SILENT_FAILURE_PATTERN.search(line) and not any(
            phrase in lowered for phrase in BENIGN_SILENT_FAILURE_PHRASES
        ):
            silent_failures += 1
        if ERROR_PATTERN.search(line) and not any(
            phrase in lowered for phrase in BENIGN_ERROR_PHRASES
        ):
            errors += 1
    return silent_failures, errors


def _scan_json_value(value: Any) -> tuple[int, int]:
    if isinstance(value, dict):
        silent_failures = 0
        errors = 0
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in {
                "silent_failure",
                "silent_failures",
                "silent_failure_count",
            }:
                silent_failures += _int_count(item)
                continue
            if normalized in {"error", "errors", "error_count"}:
                errors += _int_count(item)
                continue
            if normalized in {"failure", "failures", "failure_count"}:
                errors += _int_count(item)
                continue
            if normalized in {"exception", "traceback", "fatal"}:
                errors += _int_count(item)
                continue
            child_silent, child_errors = _scan_json_value(item)
            silent_failures += child_silent
            errors += child_errors
        return silent_failures, errors
    if isinstance(value, list):
        silent_failures = 0
        errors = 0
        for item in value:
            child_silent, child_errors = _scan_json_value(item)
            silent_failures += child_silent
            errors += child_errors
        return silent_failures, errors
    if isinstance(value, str):
        return _scan_text(value)
    return 0, 0


def _scan_source(path: Path) -> tuple[int, int]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return _scan_json_value(json.loads(text))
    if path.suffix.lower() == ".jsonl":
        silent_failures = 0
        errors = 0
        for line in text.splitlines():
            if not line.strip():
                continue
            child_silent, child_errors = _scan_json_value(json.loads(line))
            silent_failures += child_silent
            errors += child_errors
        return silent_failures, errors
    return _scan_text(text)


def build_report(
    sources: list[Path],
    *,
    target_version: str = DEFAULT_TARGET_VERSION,
    started_at_utc: dt.datetime | None = None,
    ended_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    silent_failures = 0
    errors = 0
    source_files: list[str] = []
    started_at_utc = started_at_utc or dt.datetime(2026, 5, 10, tzinfo=dt.UTC)
    ended_at_utc = ended_at_utc or dt.datetime.now(dt.UTC)
    started = started_at_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    ended = ended_at_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    if not sources:
        blockers.append("source_files_missing")

    for source in sources:
        source_files.append(str(source))
        if not source.exists() or not source.is_file():
            blockers.append(f"source_missing:{source}")
            continue
        try:
            source_silent, source_errors = _scan_source(source)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            blockers.append(f"source_unreadable:{source}:{exc.__class__.__name__}")
            continue
        silent_failures += source_silent
        errors += source_errors

    if silent_failures:
        blockers.append("silent_failures_detected")
    if errors:
        blockers.append("errors_detected")

    return {
        "schema_version": SCHEMA_VERSION,
        "target_version": target_version,
        "audit_id": "v3.12-soak-log-audit",
        "command": "python tools/run_release_soak_log_audit.py",
        "source_files": source_files,
        "source_file_count": len(source_files),
        "started_at_utc": started,
        "ended_at_utc": ended,
        "silent_failure_count": silent_failures,
        "error_count": errors,
        "error_log_clean": not blockers,
        "blockers": blockers,
        "audit_result": "pass" if not blockers else "blocked",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        type=Path,
        help="Source log/artifact to audit. Repeat for multiple files.",
    )
    parser.add_argument("--target-version", default=DEFAULT_TARGET_VERSION)
    args = parser.parse_args(argv)

    report = build_report(args.source, target_version=args.target_version)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["audit_result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
