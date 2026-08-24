#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed source and coverage binding for release soak-log audits.

Pure helper: no network, no audit re-run, no writes. It answers one
question about a stored soak-log audit report - does it attest a clean,
commit-bound audit over verified sources with continuous runtime
coverage of the required window?

The canonical artifact this hardens against claims a clean pass from
only two sources, carries no source commit or generation timestamp, and
the audit runner accepts a single synthetic clean line spanning the
nominal 336-hour window as a pass. Every failure shape maps to a
stable, path-free blocker; an empty list is returned only for a clean,
complete, exact-commit, continuously-covered report.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ALLOWED_SOURCE_SUFFIXES = (".log", ".json", ".jsonl")
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
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_ABSOLUTE_PATTERN = re.compile(r"^([A-Za-z]:|/|\\\\)")


def _append_once(blockers: list[str], blocker: str) -> None:
    if blocker not in blockers:
        blockers.append(blocker)


def _is_strict_zero_int(value: object) -> bool:
    return type(value) is int and value == 0


def _parse_utc_zero(value: object) -> dt.datetime | None:
    """Parse an explicit UTC-offset-zero timestamp; anything else is None."""
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


def _parse_record_instant(value: object) -> dt.datetime | None:
    """Parse a record timestamp: timezone-aware only, folded to UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.UTC)


def _normalized_rel_path(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.replace("\\", "/").strip()
    if _ABSOLUTE_PATTERN.match(normalized):
        return None
    parts = normalized.split("/")
    # "." components are internal aliases (logs/./x == logs/x) and are
    # rejected outright rather than silently collapsed.
    if any(part in ("", ".", "..") for part in parts):
        return None
    if not normalized.lower().endswith(ALLOWED_SOURCE_SUFFIXES):
        return None
    return normalized


def _is_reparse_point(path: Path) -> bool:
    """True for Windows reparse points (junctions included); 3.11-safe."""
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if not reparse_flag:
        return False
    try:
        attributes = getattr(
            os.lstat(path), "st_file_attributes", 0
        )
    except OSError:
        return True
    return bool(attributes & reparse_flag)


def _source_digest(path: Path) -> str | None:
    try:
        normalized = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError):
        return None
    digest = hashlib.sha256(normalized.encode("utf-8"))
    return "sha256:" + digest.hexdigest()


def _dict_record_instants(record: dict) -> list[dt.datetime] | None:
    """The single event instant of one record; None = invalid record.

    A record must carry EXACTLY ONE recognized timestamp key: zero
    means undated, and more than one is ambiguous - counting every
    timestamp field would let one synthetic record with many keys
    manufacture continuous window coverage, and would let soak-summary
    started/ended metadata masquerade as runtime heartbeats. The one
    present key must parse as a timezone-aware instant.
    """
    present = [key for key in TIMESTAMP_KEYS if key in record]
    if len(present) != 1:
        return None
    instant = _parse_record_instant(record[present[0]])
    if instant is None:
        return None
    return [instant]


def _file_record_instants(path: Path) -> list[dt.datetime] | None:
    """All record instants of one source file; None = parse failure.

    Every nonblank record must carry exactly one valid timezone-aware
    timestamp: report counts are attacker-controlled, so an undated
    record (dict without a recognized timestamp key, or a log line
    without a timestamp prefix) is a parse failure rather than a silent
    skip - otherwise undated failure records could hide inside an
    otherwise-covered file.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    instants: list[dt.datetime] = []
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                loaded = json.loads(line)
            except (json.JSONDecodeError, RecursionError):
                return None
            if not isinstance(loaded, dict):
                return None
            found = _dict_record_instants(loaded)
            if found is None:
                return None
            instants.extend(found)
        return instants
    if suffix == ".json":
        try:
            loaded = json.loads(text)
        except (json.JSONDecodeError, RecursionError):
            return None
        records = loaded if isinstance(loaded, list) else [loaded]
        for record in records:
            if not isinstance(record, dict):
                return None
            found = _dict_record_instants(record)
            if found is None:
                return None
            instants.extend(found)
        return instants
    for line in text.splitlines():
        if not line.strip():
            continue
        match = LINE_TIMESTAMP_PATTERN.match(line)
        if match is None:
            return None
        instant = _parse_record_instant(match.group("ts"))
        if instant is None:
            return None
        instants.append(instant)
    return instants


_MAX_HOURS_PARAM = 1_000_000


def _positive_finite_number(value: object) -> bool:
    # Bounded above as well: a huge int (e.g. 10**1000) is finite but
    # overflows timedelta construction, which must never raise.
    if type(value) is int:
        return 0 < value <= _MAX_HOURS_PARAM
    if type(value) is float:
        return (
            value == value
            and 0 < value <= _MAX_HOURS_PARAM
        )
    return False


def evaluate_soak_log_source_attestation(
    report_path: Path | str,
    source_root: Path | str,
    expected_commit: str,
    required_window_hours: int = 336,
    max_gap_hours: int = 24,
) -> list[str]:
    """Return stable blockers binding a soak-log audit to source truth.

    Empty list only when: the report is a readable JSON object;
    ``audit_result`` is the literal string ``pass``, ``error_log_clean``
    is literally ``True``, ``blockers`` is exactly an empty list, and
    all three counts are strict-int zero; ``source_commit`` equals the
    40-lowercase-hex ``expected_commit``; started/ended/generated
    timestamps are explicit UTC-offset-zero with the window at least
    ``required_window_hours`` and ``generated_at`` not before the end;
    the source inventory is exact, unique, normalized, confined under
    ``source_root`` (no absolute paths, traversal, symlinks, or
    aliases; only .log/.json/.jsonl) with LF-normalized sha256 hashes
    that recompute; and the union of timestamped records covers the
    window with endpoints and interior gaps within ``max_gap_hours``.
    All blockers are path-free; hostile nested types fold into
    blockers, never exceptions.
    """
    if not isinstance(expected_commit, str) or not _COMMIT_PATTERN.match(
        expected_commit
    ):
        return ["expected_commit_invalid"]

    try:
        loaded = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
    ):
        return ["soak_log_report_unreadable"]
    if not isinstance(loaded, dict):
        return ["soak_log_report_unreadable"]

    blockers: list[str] = []

    if not (
        loaded.get("schema_version")
        == "waggledance.release_soak_log_audit.v1"
        and loaded.get("audit_result") == "pass"
        and loaded.get("error_log_clean") is True
        and loaded.get("blockers") == []
        and _is_strict_zero_int(loaded.get("silent_failure_count"))
        and _is_strict_zero_int(loaded.get("error_count"))
        and _is_strict_zero_int(loaded.get("undated_record_count"))
    ):
        _append_once(blockers, "soak_log_not_clean")

    source_commit = loaded.get("source_commit")
    if source_commit is None:
        _append_once(blockers, "soak_log_source_commit_missing")
    elif (
        not isinstance(source_commit, str)
        or source_commit != expected_commit
    ):
        _append_once(blockers, "soak_log_source_commit_mismatch")

    started = _parse_utc_zero(loaded.get("started_at_utc"))
    ended = _parse_utc_zero(loaded.get("ended_at_utc"))
    window_valid = (
        _positive_finite_number(required_window_hours)
        and started is not None
        and ended is not None
        and ended > started
        and (ended - started)
        >= dt.timedelta(hours=required_window_hours)
    )
    if not window_valid:
        _append_once(blockers, "soak_log_window_invalid")

    generated = _parse_utc_zero(loaded.get("generated_at"))
    if generated is None or (
        ended is not None and generated < ended
    ):
        _append_once(blockers, "soak_log_generated_at_invalid")

    source_files = loaded.get("source_files")
    source_hashes = loaded.get("source_hashes")
    source_root_path = Path(source_root)
    bound_files: list[tuple[str, Path]] = []
    sources_bound = True
    if (
        not isinstance(source_files, list)
        or not source_files
        or not isinstance(source_hashes, dict)
        or type(loaded.get("source_file_count")) is not int
        or loaded.get("source_file_count") != len(source_files)
    ):
        sources_bound = False
    else:
        # The source root itself must not be a symlink or reparse
        # point: the parent walk below stops AT the root, so an
        # untrusted root would otherwise never be inspected.
        try:
            if source_root_path.is_symlink() or _is_reparse_point(
                source_root_path
            ):
                sources_bound = False
        except (OSError, RuntimeError):
            sources_bound = False
        seen: set[str] = set()
        seen_identities: set = set()
        for entry in source_files if sources_bound else []:
            normalized = _normalized_rel_path(entry)
            # Windows resolves paths case-insensitively: casefold the
            # alias check so LOGS/X.LOG cannot double-count logs/x.log.
            if normalized is None or normalized.casefold() in seen:
                sources_bound = False
                break
            seen.add(normalized.casefold())
            candidate = source_root_path / normalized
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    sources_bound = False
                    break
                # Reject a symlink or Windows reparse point (junction)
                # on the candidate or ANY component up to the source
                # root, not only the final file.
                component_link = _is_reparse_point(candidate)
                resolved_root = source_root_path.resolve()
                if not component_link:
                    for parent in candidate.parents:
                        if (
                            parent == source_root_path
                            or parent == resolved_root
                        ):
                            break
                        if parent.is_symlink() or _is_reparse_point(
                            parent
                        ):
                            component_link = True
                            break
                if component_link:
                    sources_bound = False
                    break
                resolved = candidate.resolve()
                if not resolved.is_relative_to(resolved_root):
                    sources_bound = False
                    break
                # Two entries naming the same underlying file (hardlink,
                # junction, or case alias) are one source counted twice.
                # Path.resolve() does NOT unify hardlinks, so identity
                # is the (device, inode) stat pair, with the resolved
                # casefolded path as a fallback where inodes are zero.
                candidate_stat = os.stat(candidate)
                if candidate_stat.st_ino:
                    identity = (
                        candidate_stat.st_dev,
                        candidate_stat.st_ino,
                    )
                else:
                    identity = str(resolved).casefold()
                if identity in seen_identities:
                    sources_bound = False
                    break
                seen_identities.add(identity)
            except (OSError, RuntimeError):
                # RuntimeError covers pathological symlink loops that
                # pathlib resolution raises on.
                sources_bound = False
                break
            bound_files.append((entry, candidate))
        if sources_bound and set(source_hashes.keys()) != {
            entry for entry, _ in bound_files
        }:
            sources_bound = False
    if not sources_bound:
        _append_once(blockers, "soak_log_sources_unbound")

    hashes_ok = sources_bound
    if sources_bound:
        for entry, candidate in bound_files:
            expected_digest = source_hashes.get(entry)
            actual_digest = _source_digest(candidate)
            if (
                not isinstance(expected_digest, str)
                or actual_digest is None
                or actual_digest != expected_digest
            ):
                hashes_ok = False
        if not hashes_ok:
            _append_once(blockers, "soak_log_source_hash_mismatch")

    if sources_bound and hashes_ok and window_valid:
        coverage_ok = _positive_finite_number(max_gap_hours)
        instants: list[dt.datetime] = []
        if coverage_ok:
            for _, candidate in bound_files:
                file_instants = _file_record_instants(candidate)
                if file_instants is None:
                    coverage_ok = False
                    break
                instants.extend(file_instants)
        if coverage_ok:
            # Only instants inside [started, ended] count toward
            # coverage: out-of-window records must not fake endpoint
            # proximity or bridge interior gaps.
            instants = sorted(
                instant
                for instant in instants
                if started <= instant <= ended
            )
            max_gap = dt.timedelta(hours=max_gap_hours)
            if not instants:
                coverage_ok = False
            elif (
                instants[0] - started > max_gap
                or ended - instants[-1] > max_gap
            ):
                coverage_ok = False
            else:
                for earlier, later in zip(instants, instants[1:]):
                    if later - earlier > max_gap:
                        coverage_ok = False
                        break
        if not coverage_ok:
            _append_once(blockers, "soak_log_coverage_insufficient")

    return blockers
