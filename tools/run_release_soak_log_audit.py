#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write v3.12 soak log audit evidence from explicit source files."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
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
TIMESTAMP_KEYS = (
    "ts",
    "ts_utc",
    "timestamp",
    "timestamp_utc",
    "time",
    "time_utc",
    "created_at",
    "created_at_utc",
    "started_at",
    "started_at_utc",
    "ended_at",
    "ended_at_utc",
    "updated_at",
    "updated_at_utc",
)
LINE_TIMESTAMP_PATTERN = re.compile(
    r"^\s*(?P<ts>\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+Z?)\b"
)


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


def _in_window(
    value: object,
    *,
    started_at_utc: dt.datetime,
    ended_at_utc: dt.datetime,
) -> bool | None:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return None
    return started_at_utc <= parsed <= ended_at_utc


def _record_in_window(
    value: dict[str, Any],
    *,
    started_at_utc: dt.datetime,
    ended_at_utc: dt.datetime,
) -> bool | None:
    for key in TIMESTAMP_KEYS:
        if key in value:
            return _in_window(
                value[key],
                started_at_utc=started_at_utc,
                ended_at_utc=ended_at_utc,
            )
    return None


def _source_digest(path: Path) -> str:
    normalized = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    digest = hashlib.sha256(normalized.encode("utf-8"))
    return "sha256:" + digest.hexdigest()


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


def _scan_text(
    text: str,
    *,
    started_at_utc: dt.datetime,
    ended_at_utc: dt.datetime,
    count_undated: bool = True,
) -> tuple[int, int, int]:
    silent_failures = 0
    errors = 0
    undated = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        match = LINE_TIMESTAMP_PATTERN.match(line)
        if match is not None:
            in_window = _in_window(
                match.group("ts"),
                started_at_utc=started_at_utc,
                ended_at_utc=ended_at_utc,
            )
            if in_window is False:
                continue
        elif count_undated:
            undated += 1
        lowered = line.lower()
        if SILENT_FAILURE_PATTERN.search(line) and not any(
            phrase in lowered for phrase in BENIGN_SILENT_FAILURE_PHRASES
        ):
            silent_failures += 1
        if ERROR_PATTERN.search(line) and not any(
            phrase in lowered for phrase in BENIGN_ERROR_PHRASES
        ):
            errors += 1
    return silent_failures, errors, undated


def _scan_json_value(
    value: Any,
    *,
    started_at_utc: dt.datetime,
    ended_at_utc: dt.datetime,
) -> tuple[int, int, int]:
    if isinstance(value, dict):
        in_window = _record_in_window(
            value,
            started_at_utc=started_at_utc,
            ended_at_utc=ended_at_utc,
        )
        silent_failures = 0
        errors = 0
        undated = 0
        if in_window is None and any(
            key not in TIMESTAMP_KEYS
            and not isinstance(item, (dict, list))
            and str(item).strip()
            for key, item in value.items()
        ):
            undated += 1
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in TIMESTAMP_KEYS:
                continue
            if normalized in {
                "silent_failure",
                "silent_failures",
                "silent_failure_count",
            }:
                if in_window is not False:
                    silent_failures += _int_count(item)
                continue
            if normalized in {"error", "errors", "error_count"}:
                if in_window is not False:
                    errors += _int_count(item)
                continue
            if normalized in {"failure", "failures", "failure_count"}:
                if in_window is not False:
                    errors += _int_count(item)
                continue
            if normalized in {"exception", "traceback", "fatal"}:
                if in_window is not False:
                    errors += _int_count(item)
                continue
            if in_window is False and not isinstance(item, (dict, list)):
                continue
            if isinstance(item, (dict, list)):
                child_silent, child_errors, child_undated = _scan_json_value(
                    item,
                    started_at_utc=started_at_utc,
                    ended_at_utc=ended_at_utc,
                )
            elif isinstance(item, str):
                child_silent, child_errors, child_undated = _scan_text(
                    item,
                    started_at_utc=started_at_utc,
                    ended_at_utc=ended_at_utc,
                    count_undated=False,
                )
            else:
                child_silent, child_errors, child_undated = 0, 0, 0
            silent_failures += child_silent
            errors += child_errors
            undated += child_undated
        return silent_failures, errors, undated
    if isinstance(value, list):
        silent_failures = 0
        errors = 0
        undated = 0
        for item in value:
            child_silent, child_errors, child_undated = _scan_json_value(
                item,
                started_at_utc=started_at_utc,
                ended_at_utc=ended_at_utc,
            )
            silent_failures += child_silent
            errors += child_errors
            undated += child_undated
        return silent_failures, errors, undated
    if isinstance(value, str):
        return _scan_text(
            value,
            started_at_utc=started_at_utc,
            ended_at_utc=ended_at_utc,
            count_undated=True,
        )
    return 0, 0, 0


def _scan_source(
    path: Path,
    *,
    started_at_utc: dt.datetime,
    ended_at_utc: dt.datetime,
) -> tuple[int, int, int]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return _scan_json_value(
            json.loads(text),
            started_at_utc=started_at_utc,
            ended_at_utc=ended_at_utc,
        )
    if path.suffix.lower() == ".jsonl":
        silent_failures = 0
        errors = 0
        undated = 0
        for line in text.splitlines():
            if not line.strip():
                continue
            child_silent, child_errors, child_undated = _scan_json_value(
                json.loads(line),
                started_at_utc=started_at_utc,
                ended_at_utc=ended_at_utc,
            )
            silent_failures += child_silent
            errors += child_errors
            undated += child_undated
        return silent_failures, errors, undated
    return _scan_text(
        text,
        started_at_utc=started_at_utc,
        ended_at_utc=ended_at_utc,
    )


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
    undated_records = 0
    source_files: list[str] = []
    source_hashes: dict[str, str] = {}
    started_at_utc = started_at_utc or dt.datetime(2026, 5, 10, tzinfo=dt.UTC)
    ended_at_utc = ended_at_utc or dt.datetime.now(dt.UTC)
    started = started_at_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    ended = ended_at_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    if not sources:
        blockers.append("source_files_missing")

    for source in sources:
        source_key = source.as_posix()
        source_files.append(source_key)
        if not source.exists() or not source.is_file():
            blockers.append(f"source_missing:{source}")
            continue
        try:
            source_hashes[source_key] = _source_digest(source)
            source_silent, source_errors, source_undated = _scan_source(
                source,
                started_at_utc=started_at_utc,
                ended_at_utc=ended_at_utc,
            )
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            blockers.append(f"source_unreadable:{source}:{exc.__class__.__name__}")
            continue
        silent_failures += source_silent
        errors += source_errors
        undated_records += source_undated

    if silent_failures:
        blockers.append("silent_failures_detected")
    if errors:
        blockers.append("errors_detected")
    if undated_records:
        blockers.append("undated_records_detected")

    return {
        "schema_version": SCHEMA_VERSION,
        "target_version": target_version,
        "audit_id": "v3.12-soak-log-audit",
        "command": "python tools/run_release_soak_log_audit.py",
        "source_files": source_files,
        "source_hashes": source_hashes,
        "source_file_count": len(source_files),
        "started_at_utc": started,
        "ended_at_utc": ended,
        "silent_failure_count": silent_failures,
        "error_count": errors,
        "undated_record_count": undated_records,
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
