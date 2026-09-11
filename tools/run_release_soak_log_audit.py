#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write v3.12 soak log audit evidence from explicit source files.

Legacy invocation (no ``--source-commit``) is unchanged and keeps producing
the ``waggledance.release_soak_log_audit.v1`` report exactly as before.

With ``--source-commit`` the report keeps the v1 schema string and adds the
``waggledance.release_soak_log_audit_fields.v2`` field set: the source
subject commit and tree, an explicit UTC window, the lock digest read from
the subject's tracked blob, per-source roles (``coverage`` versus
``diagnostic``), an append-only binding of every source against the
subject's blob, flat coverage-record validation and window coverage
statistics. Every binding failure is a fail-closed blocker. The new fields
are additive producer evidence only; nothing here implies that the release
gate consumes them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "waggledance.release_soak_log_audit.v1"
CONTRACT_VERSION = "waggledance.release_soak_log_audit_fields.v2"
DEFAULT_OUTPUT = (
    Path("docs")
    / "runs"
    / "release_soak_evidence"
    / "v3.12.0_soak_log_audit.json"
)
DEFAULT_TARGET_VERSION = "v3.12.0"
DEFAULT_LOCK_PATH = "requirements.lock.txt"
DEFAULT_REQUIRED_WINDOW_HOURS = 336
DEFAULT_MAX_GAP_HOURS = 24
ROLE_COVERAGE = "coverage"
ROLE_DIAGNOSTIC = "diagnostic"
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
# Keys whose values count as failures: the JSON scanner's counted keys (see
# ``_scan_json_value``) plus the harness counters the standalone attester
# treats the same way. On a coverage record every present counted key must
# be a strict int; a healthy heartbeat carries only zeros, and any positive
# count (for example ``error_count: 1`` on a probe failure) makes the record
# non-healthy, which blocks the audit.
COUNTED_RECORD_KEYS = frozenset({
    "silent_failure",
    "silent_failures",
    "silent_failure_count",
    "error",
    "errors",
    "error_count",
    "failure",
    "failures",
    "failure_count",
    "exception",
    "traceback",
    "fatal",
    "app_errors",
    "connection_errors",
})
COVERAGE_TIMESTAMP_KEY = "ts_utc"
COVERAGE_REQUIRED_KEYS = ("kind", "state", "source_commit", "lock_digest", "seq")
HEALTHY_COVERAGE_KIND = "soak_heartbeat"
HEALTHY_COVERAGE_STATE = "ok"
# The v3.12.0 fresh-soak contract fixes the source inventory: the two legacy
# diagnostic files stay byte-identical to the subject and exactly one typed
# coverage journal proves the window. Any other inventory, role map or lock
# path blocks, because the standalone attester rejects it anyway.
FRESH_COVERAGE_SOURCE = "docs/runs/release_soak_evidence/v3.12.0_soak_heartbeat.jsonl"
FRESH_SOURCE_ROLES = {
    "docs/runs/error_log.jsonl": ROLE_DIAGNOSTIC,
    "docs/runs/release_soak_evidence/v3.12.0_history.jsonl": ROLE_DIAGNOSTIC,
    FRESH_COVERAGE_SOURCE: ROLE_COVERAGE,
}
ALLOWED_SOURCE_SUFFIXES = (".log", ".json", ".jsonl")
GIT_EXECUTABLE = "git"
GIT_TIMEOUT_SECONDS = 120
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_ABSOLUTE_PATTERN = re.compile(r"^([A-Za-z]:|/|\\\\)")
_REGULAR_BLOB_MODES = frozenset({b"100644", b"100755"})
_MAX_HOURS_PARAM = 1_000_000


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
    source_root: Path | str | None = None,
) -> dict[str, Any]:
    """Build the v1 audit report.

    ``source_root`` is only used by the bound producer: when given, each
    source path is read relative to it while the report keys keep the
    given relative path. Without it the behaviour is the legacy one.
    """
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
        resolved = source if source_root is None else Path(source_root) / source
        if not resolved.exists() or not resolved.is_file():
            blockers.append(f"source_missing:{source}")
            continue
        try:
            source_hashes[source_key] = _source_digest(resolved)
            source_silent, source_errors, source_undated = _scan_source(
                resolved,
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


# --- bound producer (v2 field set) -------------------------------------------


def _clean_git_environment() -> dict[str, str]:
    """The process environment without any ``GIT_*`` variable."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git(
    root: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[bytes] | None:
    """Run git as argv pinned to ``root``; ``None`` when git cannot be run.

    Same pinning as the release verifier: ``--git-dir root/.git`` (a linked
    worktree gitfile is honored), ``--work-tree root``, no optional locks and
    a ``GIT_*``-free environment.
    """
    command = [
        GIT_EXECUTABLE,
        "-C",
        str(root),
        "--git-dir",
        str(root / ".git"),
        "--work-tree",
        str(root),
        "--no-optional-locks",
        *args,
    ]
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            cwd=str(root),
            env=_clean_git_environment(),
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _git_exact_line(output: bytes, pattern: re.Pattern[str]) -> str | None:
    if not output.endswith(b"\n"):
        return None
    try:
        text = output[:-1].decode("ascii")
    except UnicodeDecodeError:
        return None
    return text if pattern.match(text) else None


def _git_resolve(root: Path, revision: str) -> str | None:
    completed = _run_git(root, "rev-parse", "--verify", "--quiet", revision)
    if completed is None or completed.returncode != 0:
        return None
    return _git_exact_line(completed.stdout, _COMMIT_PATTERN)


def _git_tracked_blob(
    root: Path, commit: str, rel: str
) -> tuple[bytes | None, str | None, str | None]:
    """``(bytes, blob_oid, None)`` for the regular blob at ``commit:rel``.

    The bytes come from the object store (``ls-tree`` then ``cat-file``),
    never from the worktree. Otherwise ``(None, None, blocker)``.
    """
    completed = _run_git(root, "ls-tree", "-z", commit, "--", rel)
    if completed is None:
        return None, None, "git_unavailable"
    if completed.returncode != 0:
        return None, None, "git_ls_tree_failed"
    entries = [entry for entry in completed.stdout.split(b"\0") if entry]
    if len(entries) != 1:
        return None, None, "not_tracked_at_subject"
    meta, separator, path = entries[0].partition(b"\t")
    parts = meta.split(b" ")
    if not separator or len(parts) != 3 or path != rel.encode("utf-8"):
        return None, None, "not_tracked_at_subject"
    mode, kind, object_id = parts
    if kind != b"blob" or mode not in _REGULAR_BLOB_MODES:
        return None, None, "not_regular_at_subject"
    try:
        object_text = object_id.decode("ascii")
    except UnicodeDecodeError:
        return None, None, "git_ls_tree_malformed"
    if not _COMMIT_PATTERN.match(object_text):
        return None, None, "git_ls_tree_malformed"
    completed = _run_git(root, "cat-file", "blob", object_text)
    if completed is None:
        return None, None, "git_unavailable"
    if completed.returncode != 0:
        return None, None, "git_cat_file_failed"
    return completed.stdout, object_text, None


def _git_changed_tracked_paths(root: Path) -> list[str] | None:
    """Tracked paths that differ from HEAD in index or worktree; ``None`` on error."""
    completed = _run_git(
        root, "status", "--porcelain=v1", "-z", "--untracked-files=no"
    )
    if completed is None or completed.returncode != 0:
        return None
    tokens = completed.stdout.split(b"\0")
    paths: list[str] = []
    index = 0
    while index < len(tokens):
        entry = tokens[index]
        index += 1
        if len(entry) < 4:
            continue
        status = entry[:2]
        paths.append(entry[3:].decode("utf-8", "replace").replace("\\", "/"))
        if status[:1] in (b"R", b"C") and index < len(tokens):
            paths.append(tokens[index].decode("utf-8", "replace").replace("\\", "/"))
            index += 1
    return paths


def _normalize_rel_path(
    value: object, *, suffixes: tuple[str, ...] | None = ALLOWED_SOURCE_SUFFIXES
) -> str | None:
    """Repo-relative POSIX path (with an allowed suffix when given), else ``None``."""
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.replace("\\", "/").strip()
    if _ABSOLUTE_PATTERN.match(normalized):
        return None
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    if suffixes is not None and not normalized.lower().endswith(suffixes):
        return None
    return normalized


def _lf_text(data: bytes) -> str | None:
    """UTF-8 text with CRLF folded to LF (the audit digest convention)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text.replace("\r\n", "\n")


def _text_digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_utc_zero(value: object) -> dt.datetime | None:
    """Parse an explicit UTC-offset-zero timestamp; anything else is ``None``."""
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        return None
    return parsed


def _format_utc(value: dt.datetime) -> str:
    return (
        value.astimezone(dt.UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def validate_coverage_record(
    record: object,
    *,
    source_commit: str | None = None,
    lock_digest: str | None = None,
) -> str | None:
    """Reason a coverage record is invalid, or ``None`` when it is flat and bound.

    Rules (what the scanner and the standalone attester need): a JSON object;
    exactly one recognized timestamp key, which must be ``ts_utc`` with an
    explicit UTC-zero value; every value a scalar (a nested object or list
    would be scanned as an undated record); every present counted key a
    non-negative strict int; no scanner word in any string; ``kind``,
    ``state``, ``source_commit``, ``lock_digest`` and ``seq`` present, with
    ``source_commit``/``lock_digest`` equal to the report's values when
    given and ``seq`` a non-negative int.
    """
    if not isinstance(record, dict):
        return "record_not_object"
    present = [key for key in TIMESTAMP_KEYS if key in record]
    if len(present) != 1:
        return f"timestamp_key_count:{len(present)}"
    if present[0] != COVERAGE_TIMESTAMP_KEY:
        return f"timestamp_key:{present[0]}"
    if _parse_utc_zero(record[COVERAGE_TIMESTAMP_KEY]) is None:
        return "timestamp_not_utc_zero"
    for key, value in record.items():
        if isinstance(value, (dict, list, tuple)):
            return f"nested_value:{key}"
        if value is None:
            return f"null_value:{key}"
        lowered = str(key).lower()
        if lowered in COUNTED_RECORD_KEYS and (type(value) is not int or value < 0):
            return f"counted_value_invalid:{key}"
        if isinstance(value, str) and (
            ERROR_PATTERN.search(value) or SILENT_FAILURE_PATTERN.search(value)
        ):
            return f"scanner_word:{key}"
    for required in COVERAGE_REQUIRED_KEYS:
        if required not in record:
            return f"missing_key:{required}"
    if not isinstance(record["kind"], str) or not isinstance(record["state"], str):
        return "kind_or_state_not_string"
    if source_commit is not None and record["source_commit"] != source_commit:
        return "source_commit_mismatch"
    if lock_digest is not None and record["lock_digest"] != lock_digest:
        return "lock_digest_mismatch"
    if type(record["seq"]) is not int or record["seq"] < 0:
        return "seq_invalid"
    return None


def is_healthy_coverage_record(record: dict[str, Any]) -> bool:
    """Only healthy heartbeats count as coverage; failure records never do.

    Healthy means ``kind == soak_heartbeat``, ``state == ok`` and every
    present counted key a strict-int zero (the standalone attester's rule).
    """
    if (
        record.get("kind") != HEALTHY_COVERAGE_KIND
        or record.get("state") != HEALTHY_COVERAGE_STATE
    ):
        return False
    return all(
        type(value) is int and value == 0
        for key, value in record.items()
        if str(key).lower() in COUNTED_RECORD_KEYS
    )


def _coverage_instants(
    text_lf: str,
    *,
    source_commit: str | None,
    lock_digest: str | None,
) -> tuple[list[dt.datetime], dict[str, int], str | None]:
    """Healthy-record instants and counts of a coverage file, or a reason.

    Every line must be a valid bound record (no blank lines, no bare CR,
    complete trailing newline); ``seq`` and ``ts_utc`` must be strictly
    increasing across all records; only healthy records contribute instants.
    """
    counts = {"records_total": 0, "records_healthy": 0, "records_nonhealthy": 0}
    if "\r" in text_lf:
        return [], counts, "bare_cr"
    if not text_lf:
        return [], counts, None
    if not text_lf.endswith("\n"):
        return [], counts, "incomplete_tail"
    instants: list[dt.datetime] = []
    last_seq: int | None = None
    last_instant: dt.datetime | None = None
    for number, line in enumerate(text_lf[:-1].split("\n"), 1):
        if not line.strip():
            return [], counts, f"line_{number}:blank_record"
        try:
            loaded = json.loads(line)
        except (json.JSONDecodeError, RecursionError):
            return [], counts, f"line_{number}:not_json"
        reason = validate_coverage_record(
            loaded, source_commit=source_commit, lock_digest=lock_digest
        )
        if reason is not None:
            return [], counts, f"line_{number}:{reason}"
        parsed = _parse_utc_zero(loaded[COVERAGE_TIMESTAMP_KEY])
        assert parsed is not None
        instant = parsed.astimezone(dt.UTC)
        if last_seq is not None and loaded["seq"] <= last_seq:
            return [], counts, f"line_{number}:seq_not_increasing"
        if last_instant is not None and instant <= last_instant:
            return [], counts, f"line_{number}:timestamp_not_increasing"
        last_seq = loaded["seq"]
        last_instant = instant
        counts["records_total"] += 1
        if is_healthy_coverage_record(loaded):
            counts["records_healthy"] += 1
            instants.append(instant)
        else:
            counts["records_nonhealthy"] += 1
    return instants, counts, None


def _coverage_window_stats(
    instants: list[dt.datetime],
    *,
    started_at_utc: dt.datetime,
    ended_at_utc: dt.datetime,
    max_gap_hours: int,
) -> tuple[dict[str, Any], str | None]:
    inside = sorted(
        instant for instant in instants if started_at_utc <= instant <= ended_at_utc
    )
    stats: dict[str, Any] = {
        "records_in_window": len(inside),
        "first_in_window": _format_utc(inside[0]) if inside else None,
        "last_in_window": _format_utc(inside[-1]) if inside else None,
        "max_gap_seconds": None,
    }
    if not inside:
        return stats, "coverage_empty"
    gaps = [
        (inside[0] - started_at_utc).total_seconds(),
        (ended_at_utc - inside[-1]).total_seconds(),
    ]
    gaps.extend(
        (later - earlier).total_seconds()
        for earlier, later in zip(inside, inside[1:])
    )
    stats["max_gap_seconds"] = int(max(gaps))
    if max(gaps) > dt.timedelta(hours=max_gap_hours).total_seconds():
        return stats, "coverage_gap_exceeded"
    return stats, None


def _append_only_split(subject_lf: str, current_lf: str) -> tuple[str | None, str | None]:
    """The text appended after the subject blob, or a reason it is not append-only."""
    if not current_lf.startswith(subject_lf):
        return None, "not_prefix"
    appended = current_lf[len(subject_lf):]
    if not appended:
        return "", None
    if subject_lf and not subject_lf.endswith("\n"):
        return None, "subject_without_trailing_newline"
    if not appended.endswith("\n"):
        return None, "incomplete_tail"
    return appended, None


def _complete_jsonl(appended: str) -> str | None:
    for number, line in enumerate(appended.split("\n"), 1):
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except (json.JSONDecodeError, RecursionError):
            return f"appended_line_{number}:not_json"
        if not isinstance(loaded, dict):
            return f"appended_line_{number}:not_object"
    return None


def _positive_hours(value: object) -> bool:
    return type(value) is int and 0 < value <= _MAX_HOURS_PARAM


def _regular_current_source(root: Path, relative: str) -> bool:
    """Reject redirected or non-regular source paths before filesystem reads.

    This is a local path check, not proof against concurrent host mutation;
    the release consumer must independently attest its final input snapshot.
    """
    current = root
    parts = Path(relative).parts
    try:
        for index in range(len(parts) + 1):
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                return False
            if index == len(parts):
                return stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            if not stat.S_ISDIR(info.st_mode):
                return False
            current = current / parts[index]
    except (OSError, ValueError):
        return False
    return False


def build_bound_report(
    sources: list[Path],
    *,
    source_root: Path | str,
    source_commit: object,
    coverage_sources: list[Path],
    started_at_utc: dt.datetime | None,
    ended_at_utc: dt.datetime | None,
    required_window_hours: int = DEFAULT_REQUIRED_WINDOW_HOURS,
    max_gap_hours: int = DEFAULT_MAX_GAP_HOURS,
    lock_path: str = DEFAULT_LOCK_PATH,
    target_version: str = DEFAULT_TARGET_VERSION,
    generated_at: dt.datetime | None = None,
) -> dict[str, Any]:
    """Build the v1 report plus the v2 binding fields; every failure blocks.

    ``sources`` and ``coverage_sources`` are repo-relative paths under
    ``source_root``; ``source_commit`` must be the clean HEAD of that
    checkout. The only path allowed to differ from the subject is the single
    coverage source, and only by appended complete JSON lines.
    """
    root = Path(source_root)
    blockers: list[str] = []
    generated = generated_at or dt.datetime.now(dt.UTC)

    commit = source_commit if isinstance(source_commit, str) else ""
    if not _COMMIT_PATTERN.match(commit):
        blockers.append("source_commit_invalid")
        commit = ""

    # Window: explicit, ordered, long enough; generation after the end.
    window_hours: float | None = None
    if started_at_utc is None or ended_at_utc is None:
        blockers.append("window_explicit_required")
    elif ended_at_utc <= started_at_utc:
        blockers.append("window_invalid")
    else:
        window_hours = (ended_at_utc - started_at_utc).total_seconds() / 3600.0
        if not _positive_hours(required_window_hours):
            blockers.append("required_window_hours_invalid")
        elif window_hours < required_window_hours:
            blockers.append("window_shorter_than_required")
        if generated < ended_at_utc:
            blockers.append("generated_before_end")
    if not _positive_hours(max_gap_hours) or max_gap_hours > DEFAULT_MAX_GAP_HOURS:
        blockers.append("max_gap_hours_invalid")

    # Sources and roles.
    normalized_sources: list[str] = []
    source_paths: list[Path] = []
    seen: set[str] = set()
    for source in sources:
        normalized = _normalize_rel_path(source.as_posix())
        if normalized is None or normalized.casefold() in seen:
            blockers.append(f"source_path_invalid:{source.as_posix()}")
            continue
        seen.add(normalized.casefold())
        normalized_sources.append(normalized)
        source_paths.append(Path(normalized))
    coverage_keys: list[str] = []
    for source in coverage_sources:
        normalized = _normalize_rel_path(source.as_posix())
        if normalized is None or normalized not in normalized_sources:
            blockers.append(f"coverage_source_not_listed:{source.as_posix()}")
            continue
        if not normalized.lower().endswith(".jsonl"):
            blockers.append(f"coverage_source_not_jsonl:{normalized}")
            continue
        if normalized not in coverage_keys:
            coverage_keys.append(normalized)
    if not coverage_keys:
        blockers.append("coverage_source_missing")
    elif len(coverage_keys) != 1:
        blockers.append("coverage_source_count_invalid")
    roles = {
        key: ROLE_COVERAGE if key in coverage_keys else ROLE_DIAGNOSTIC
        for key in normalized_sources
    }
    if roles != FRESH_SOURCE_ROLES or coverage_keys != [FRESH_COVERAGE_SOURCE]:
        blockers.append("source_inventory_not_fresh_contract")
    if lock_path != DEFAULT_LOCK_PATH:
        blockers.append("lock_path_not_fresh_contract")

    readable_sources = []
    for source_path in source_paths:
        if _regular_current_source(root, source_path.as_posix()):
            readable_sources.append(source_path)
        else:
            blockers.append(f"current_source_not_regular:{source_path.as_posix()}")
    report = build_report(
        readable_sources,
        target_version=target_version,
        started_at_utc=started_at_utc,
        ended_at_utc=ended_at_utc,
        source_root=root,
    )

    # Git binding: HEAD, cleanliness, lock, subject blobs, coverage.
    head = _git_resolve(root, "HEAD^{commit}")
    source_tree: str | None = None
    changed_paths: list[str] = []
    binding: dict[str, Any] = {}
    coverage: dict[str, Any] = {}
    lock_blob: str | None = None
    lock_digest: str | None = None
    if head is None:
        blockers.append("git_unavailable")
    else:
        if commit and head != commit:
            blockers.append("source_commit_not_head")
        tree_completed = _run_git(
            root, "rev-parse", "--verify", "--quiet", "HEAD^{tree}"
        )
        if tree_completed is not None and tree_completed.returncode == 0:
            source_tree = _git_exact_line(tree_completed.stdout, _COMMIT_PATTERN)
        changed = _git_changed_tracked_paths(root)
        if changed is None:
            blockers.append("git_status_failed")
        else:
            changed_paths = sorted(set(changed))
            for path in changed_paths:
                if path in coverage_keys:
                    continue
                if path in roles:
                    blockers.append(f"diagnostic_source_modified:{path}")
                blockers.append("source_worktree_dirty")
        subject = commit or head
        lock_rel = _normalize_rel_path(lock_path, suffixes=None)
        if lock_rel is None or lock_rel != DEFAULT_LOCK_PATH:
            blockers.append("lock_blob_unreadable")
        else:
            data, lock_blob, blocker = _git_tracked_blob(root, subject, lock_rel)
            lock_lf = _lf_text(data) if data is not None else None
            if blocker is not None or lock_lf is None:
                lock_blob = None
                blockers.append("lock_blob_unreadable")
            else:
                lock_digest = _text_digest(lock_lf)
        for key in normalized_sources:
            role = roles[key]
            data, blob, blocker = _git_tracked_blob(root, subject, key)
            entry: dict[str, Any] = {
                "role": role,
                "subject_blob": blob,
                "subject_line_count": None,
                "appended_line_count": None,
                "append_only": False,
            }
            binding[key] = entry
            if blocker is not None or data is None:
                blockers.append(f"raw_log_missing_at_subject:{key}")
                continue
            subject_lf = _lf_text(data)
            if subject_lf is None:
                blockers.append(f"raw_log_subject_not_utf8:{key}")
                continue
            entry["subject_line_count"] = subject_lf.count("\n")
            entry["subject_digest"] = _text_digest(subject_lf)
            current_path = root / key
            if not _regular_current_source(root, key):
                blockers.append(f"current_source_not_regular:{key}")
                continue
            try:
                current_lf = _lf_text(current_path.read_bytes())
            except OSError:
                current_lf = None
            if current_lf is None:
                # The legacy scan already reported the file as missing or
                # unreadable; the binding stays fail-closed without a second
                # path-bearing blocker.
                continue
            appended, reason = _append_only_split(subject_lf, current_lf)
            if appended is None:
                blockers.append(f"raw_log_not_append_only:{key}")
                continue
            entry["appended_line_count"] = appended.count("\n")
            if role == ROLE_DIAGNOSTIC:
                if appended:
                    blockers.append(f"diagnostic_source_modified:{key}")
                    continue
            elif _complete_jsonl(appended) is not None:
                blockers.append(f"raw_log_not_append_only:{key}")
                continue
            entry["append_only"] = True
            if role != ROLE_COVERAGE:
                continue
            instants, counts, reason = _coverage_instants(
                current_lf,
                source_commit=commit or None,
                lock_digest=lock_digest,
            )
            if reason is not None:
                coverage[key] = {**counts, "invalid": reason}
                blockers.append(f"coverage_record_invalid:{key}")
                continue
            if counts["records_nonhealthy"]:
                # The standalone attester rejects every non-healthy record;
                # a degraded or stopped record must block even when other
                # records would still cover the window.
                blockers.append(f"coverage_nonhealthy_records:{key}")
            if (
                started_at_utc is not None
                and ended_at_utc is not None
                and ended_at_utc > started_at_utc
                and _positive_hours(max_gap_hours)
            ):
                stats, reason = _coverage_window_stats(
                    instants,
                    started_at_utc=started_at_utc,
                    ended_at_utc=ended_at_utc,
                    max_gap_hours=max_gap_hours,
                )
                coverage[key] = {**counts, **stats}
                if reason is not None:
                    blockers.append(f"{reason}:{key}")
            else:
                coverage[key] = dict(counts)

    merged: list[str] = []
    for blocker in [*report["blockers"], *blockers]:
        if blocker not in merged:
            merged.append(blocker)
    report.update({
        "contract_version": CONTRACT_VERSION,
        "source_commit": commit or None,
        "source_tree": source_tree,
        "generated_at": _format_utc(generated),
        "window_hours": window_hours,
        "required_window_hours": required_window_hours,
        "max_gap_hours": max_gap_hours,
        "source_roles": roles,
        "coverage_sources": coverage_keys,
        "lock_path": lock_path,
        "lock_blob": lock_blob,
        "lock_digest": lock_digest,
        "raw_log_binding": binding,
        "coverage": coverage,
        "worktree": {"head": head, "changed_tracked_paths": changed_paths},
        "producer": {
            "tool": "tools/run_release_soak_log_audit.py",
            "python": sys.version.split()[0],
            "platform": sys.platform,
        },
        "blockers": merged,
        "error_log_clean": not merged,
        "audit_result": "pass" if not merged else "blocked",
    })
    return report


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
    bound = parser.add_argument_group(
        "source binding (v2 field set; requires --source-commit)"
    )
    bound.add_argument(
        "--source-commit",
        default=None,
        help="Clean HEAD commit of --source-root; enables the bound report.",
    )
    bound.add_argument(
        "--source-root",
        type=Path,
        default=ROOT,
        help="Checkout that --source paths are relative to (default: repo root).",
    )
    bound.add_argument("--started-at-utc", default=None)
    bound.add_argument("--ended-at-utc", default=None)
    bound.add_argument(
        "--required-window-hours", type=int, default=DEFAULT_REQUIRED_WINDOW_HOURS
    )
    bound.add_argument("--max-gap-hours", type=int, default=DEFAULT_MAX_GAP_HOURS)
    bound.add_argument(
        "--coverage-source",
        action="append",
        default=[],
        type=Path,
        help="Source (also listed via --source) whose records prove coverage.",
    )
    bound.add_argument("--lock-path", default=DEFAULT_LOCK_PATH)
    args = parser.parse_args(argv)

    if args.source_commit is None:
        if (
            args.coverage_source
            or args.started_at_utc is not None
            or args.ended_at_utc is not None
        ):
            parser.error(
                "--coverage-source, --started-at-utc and --ended-at-utc require "
                "--source-commit"
            )
        report = build_report(args.source, target_version=args.target_version)
    else:
        started = _parse_utc_zero(args.started_at_utc)
        ended = _parse_utc_zero(args.ended_at_utc)
        report = build_bound_report(
            args.source,
            source_root=args.source_root,
            source_commit=args.source_commit,
            coverage_sources=args.coverage_source,
            started_at_utc=started,
            ended_at_utc=ended,
            required_window_hours=args.required_window_hours,
            max_gap_hours=args.max_gap_hours,
            lock_path=args.lock_path,
            target_version=args.target_version,
        )
        for flag, value, parsed in (
            ("started_at_utc", args.started_at_utc, started),
            ("ended_at_utc", args.ended_at_utc, ended),
        ):
            if value is not None and parsed is None:
                blocker = f"window_invalid:{flag}"
                if blocker not in report["blockers"]:
                    report["blockers"].append(blocker)
                report["error_log_clean"] = False
                report["audit_result"] = "blocked"
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["audit_result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
