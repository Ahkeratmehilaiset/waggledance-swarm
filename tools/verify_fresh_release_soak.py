#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline, policy-bound v3.12.0 fresh-soak snapshot verification.

Dormant: not wired into the canonical release/merge gates. A pass verifies
local report consistency, not real elapsed runtime, signed process provenance,
or release authority. The caller supplies the independently selected subject
commit and must supply an isolated snapshot. Before/after checks detect observed
drift; they are not an atomic lock against concurrent hostile host mutation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.release_soak_append_only import MAX_SOAK_APPEND_BYTES, evaluate_soak_append_only
from tools.release_soak_log_attestation import (
    FRESH_COVERAGE_SOURCE,
    FRESH_SOURCE_ROLES,
    evaluate_soak_log_source_attestation,
)
from tools.run_release_soak_log_audit import (
    _git_changed_tracked_paths,
    _git_resolve,
    _git_tracked_blob,
    build_bound_report,
)

REQUIRED_WINDOW_HOURS = 336
MAX_GAP_HOURS = 24
MAX_REPORT_BYTES = 1024 * 1024
_REBUILT_FIELDS = (
    "schema_version", "contract_version", "target_version", "source_commit",
    "source_tree", "source_files", "source_hashes", "source_file_count",
    "source_roles", "coverage_sources", "lock_path", "lock_blob", "lock_digest",
    "window_hours", "required_window_hours", "max_gap_hours", "raw_log_binding",
    "coverage", "worktree", "silent_failure_count", "error_count",
    "undated_record_count", "error_log_clean", "audit_result", "blockers",
)


def _read_plain(path: Path, limit: int) -> bytes:
    """Bounded read; reject links/reparse ancestors and multiply named files."""
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise ValueError("redirected input")
        if component != path and not stat.S_ISDIR(info.st_mode):
            raise ValueError("non-directory ancestor")
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise ValueError("non-regular or oversized input")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("oversized input")
    return data


def _reject_constant(value: str):
    raise ValueError("non-standard JSON constant")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _instant(value):
    if not isinstance(value, str):
        raise ValueError("timestamp type")
    instant = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() != dt.timedelta(0):
        raise ValueError("timestamp must be explicit UTC")
    return instant


def _result(blockers: list[str], expected_commit: str) -> dict:
    return {
        "schema_version": "waggledance.fresh_soak_snapshot_verification.v1",
        "decision": "hold" if blockers else "pass",
        "blockers": list(dict.fromkeys(blockers)),
        "proof_scope": "offline_fresh_soak_snapshot",
        "release_authorized": False,
        "expected_commit": expected_commit,
        "target_version": "v3.12.0",
        "required_window_hours": REQUIRED_WINDOW_HOURS,
        "max_gap_hours": MAX_GAP_HOURS,
    }


def evaluate_fresh_release_soak(
    report_path: Path | str, source_root: Path | str, expected_commit: str,
) -> dict:
    """Re-attest a fresh report under fixed minimum release policy, read-only."""
    if not isinstance(expected_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        return _result(["expected_commit_invalid"], "")
    try:
        path = Path(report_path).absolute()
        root = Path(source_root).absolute()
        report_bytes = _read_plain(path, MAX_REPORT_BYTES)
        loaded = json.loads(report_bytes.decode("utf-8"),
                            parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        if not isinstance(loaded, dict):
            raise ValueError("report must be an object")
    except (OSError, ValueError, TypeError, RecursionError):
        return _result(["soak_consumer_report_unreadable"], expected_commit)

    blockers = []
    try:
        started = _instant(loaded.get("started_at_utc"))
        ended = _instant(loaded.get("ended_at_utc"))
        generated = _instant(loaded.get("generated_at"))
        if not started < ended <= generated <= dt.datetime.now(dt.UTC):
            raise ValueError("invalid time ordering")
    except (ValueError, OverflowError):
        return _result(["soak_consumer_time_invalid"], expected_commit)

    duration = loaded.get("required_window_hours")
    gap = loaded.get("max_gap_hours")
    # Stronger stored policy is allowed. A caller/report can never lower the
    # release minimum or raise its permitted gap, including via bool-as-int.
    policy_valid = (
        type(duration) is int and REQUIRED_WINDOW_HOURS <= duration <= 1_000_000
        and type(gap) is int and 0 < gap <= MAX_GAP_HOURS
    )
    if not policy_valid:
        blockers.append("soak_consumer_policy_invalid")
    duration = duration if policy_valid else REQUIRED_WINDOW_HOURS
    gap = gap if policy_valid else MAX_GAP_HOURS

    try:
        inputs = [root / key for key in FRESH_SOURCE_ROLES]
        inputs.append(root / "requirements.lock.txt")
        before = {item: _read_plain(item, MAX_SOAK_APPEND_BYTES) for item in inputs}
        blockers.extend(evaluate_soak_log_source_attestation(
            path, root, expected_commit, required_window_hours=duration,
            max_gap_hours=gap, require_fresh_contract=True,
        ))
        rebuilt = build_bound_report(
            [Path(key) for key in FRESH_SOURCE_ROLES], source_root=root,
            source_commit=expected_commit, coverage_sources=[Path(FRESH_COVERAGE_SOURCE)],
            started_at_utc=started, ended_at_utc=ended, generated_at=generated,
            required_window_hours=duration, max_gap_hours=gap,
            target_version="v3.12.0",
        )
        blockers.extend("soak_consumer_rebuild:" + item for item in rebuilt["blockers"])
        for field in _REBUILT_FIELDS:
            # JSON representation prevents True == 1 from accepting forged types.
            if field not in loaded or json.dumps(loaded[field], sort_keys=True) != json.dumps(
                rebuilt[field], sort_keys=True,
            ):
                blockers.append("soak_consumer_binding_mismatch:" + field)
        subject_bytes, _, error = _git_tracked_blob(root, expected_commit, FRESH_COVERAGE_SOURCE)
        if error or subject_bytes is None:
            blockers.append("soak_consumer_subject_coverage_unreadable")
        else:
            blockers.extend(evaluate_soak_append_only(
                subject_bytes, before[root / FRESH_COVERAGE_SOURCE],
            ))
        if (
            _read_plain(path, MAX_REPORT_BYTES) != report_bytes
            or any(_read_plain(item, MAX_SOAK_APPEND_BYTES) != before[item] for item in inputs)
            or _git_resolve(root, "HEAD^{commit}") != expected_commit
            or _git_changed_tracked_paths(root) != rebuilt["worktree"]["changed_tracked_paths"]
        ):
            blockers.append("soak_consumer_snapshot_changed")
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        blockers.append("soak_consumer_verification_failed")
    return _result(blockers, expected_commit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args(argv)
    result = evaluate_fresh_release_soak(args.report, args.source_root, args.expected_commit)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
