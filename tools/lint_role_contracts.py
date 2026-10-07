#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""F2: lint the repository-owned bridge role contracts; print their hashes and role receipts.

Read-only: it reads ``.agent-bridge/contracts`` (the common contract ``role-contract.v1.md``
and one ``roles/<role>.v1.md`` per role) and writes nothing. A contract's hash is the SHA-256
of its bytes with every CRLF normalized to LF, which equals the git blob bytes, so a Windows
checkout (``core.autocrlf``) and a Linux checkout agree. A launcher verifies that hash (F2
wiring, launcher owner); a contract grants no authority.

Rules; every violation is reported, never repaired:

- exactly the expected files, each a regular file (no link or reparse point), at most 32 KiB;
- strict UTF-8, ASCII only, no BOM, LF or CRLF line endings only (a lone CR is refused) and a
  final newline;
- the common contract carries its marker line, every required section and the required
  phrases (the pinned helpers and Python wrapper, the request-turn and task-reply writers, the
  event-driven Monitor, native/default models, review independence);
- a role file starts with its title, carries ``contract: wd.bridge-role.v1 role=<role>``, its
  sections and a reference to the common contract;
- no file pins a model or an effort, schedules an idle timer or a self-wake, runs a helper
  from a worktree path, runs a bridge tool with a bare interpreter, or names a user-profile
  path.

``role_receipts`` turns EXPLICIT worker-to-role assignments into ``wd.role-contract-receipt.v1``
records ``{worker, roles, observed_utc, verified, source_digest, contracts, errors}``.
``verified`` means only that the repository contracts for exactly those roles pass this lint
at ``source_digest``: it is not an observation of a running worker, it never infers a role, and
it grants nothing. An unknown role is never verified.

Exit codes: 0 clean, 1 violations, 2 usage error or unreadable contracts directory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Mapping, Sequence

SCHEMA = "wd.role-contracts-lint.v1"
RECEIPT_SCHEMA = "wd.role-contract-receipt.v1"
COMMON = "role-contract.v1.md"
ROLES = ("fable-producer", "lead-impl", "rco-security", "tools-tests")
EXPECTED = (COMMON,) + tuple("roles/" + role + ".v1.md" for role in ROLES)
MAX_BYTES = 32 * 1024
DEFAULT_DIR = Path(__file__).resolve().parents[1] / ".agent-bridge" / "contracts"
BOM = b"\xef\xbb\xbf"
WORKER = re.compile(r"[a-z][a-z0-9_-]{1,32}")
COMMON_MARKER = "contract: wd.bridge-role-contract.v1"
COMMON_SECTIONS = ("## Identity", "## Bridge helpers", "## Requests and replies", "## Waking", "## Models",
                   "## Claims", "## Source and git", "## Tests", "## Review independence", "## Checkpoints",
                   "## Authority limits")
COMMON_PHRASES = ("$env:WD_BRIDGE_BIN", "$env:WD_BRIDGE_PYTHON_WRAPPER", "Start-BridgeRequestTurn.ps1",
                  "Write-BridgeTaskReply.ps1", "Monitor-AgentBridge.ps1", "native/default", "never its reviewer")
ROLE_SECTIONS = ("## Mission", "## Defaults", "## Outputs")
FORBIDDEN = (
    ("model_pin", re.compile(r"\b(?:claude-(?:opus|sonnet|haiku|fable)-\d|gpt-\d|grok-\d)", re.IGNORECASE)),
    ("model_flag", re.compile(r"--model\b|--effort\b|(?<![\w/])/model\b|\bmodel\s*=|\beffortLevel\b", re.IGNORECASE)),
    ("idle_timer", re.compile(r"\b(?:CronCreate|CronDelete|CronList|ScheduleWakeup)\b|(?<![\w/])/loop\b",
                              re.IGNORECASE)),
    ("poll_cadence", re.compile(r"\bevery\s+\d+\s*(?:s|secs?|seconds?|m|mins?|minutes?)\b", re.IGNORECASE)),
    ("worktree_helper", re.compile(r"\.[\\/]\.agent-bridge[\\/]bin[\\/]", re.IGNORECASE)),
    ("bare_interpreter", re.compile(r"\bpython(?:3|\.exe)?\s+(?:-m\s+)?tools[\\/.]", re.IGNORECASE)),
    ("user_profile_path", re.compile(r"\b[A-Za-z]:\\Users\\", re.IGNORECASE)),
)


def _error(errors: list, path: str, rule: str, line: int | None = None, detail: str = "") -> None:
    errors.append({"path": path, "rule": rule, "line": line, "detail": detail[:200]})


def _regular(path: Path) -> bool:
    """A regular file that is not a link or a reparse point (never followed)."""
    info = os.lstat(path)
    reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISREG(info.st_mode) and not reparse


def _inventory(directory: Path, errors: list) -> None:
    """Every entry under the directory must be an expected file or the ``roles`` directory."""
    for current, dirs, files in os.walk(directory, followlinks=False):
        base = Path(current)
        for name in sorted(dirs):
            rel = (base / name).relative_to(directory).as_posix()
            info = os.lstat(base / name)
            reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if rel != "roles" or reparse or not stat.S_ISDIR(info.st_mode):
                _error(errors, rel, "unexpected_entry")
                dirs.remove(name)
        for name in sorted(files):
            rel = (base / name).relative_to(directory).as_posix()
            if rel not in EXPECTED:
                _error(errors, rel, "unexpected_entry")


def _text(directory: Path, rel: str, errors: list) -> tuple[str | None, bytes | None]:
    """The LF-normalized text and bytes of one contract, or (None, None) after recording why not."""
    path = directory.joinpath(*rel.split("/"))
    try:
        if not _regular(path):
            _error(errors, rel, "not_a_regular_file")
            return None, None
        with open(path, "rb") as stream:
            raw = stream.read(2 * MAX_BYTES + 1)
    except FileNotFoundError:
        _error(errors, rel, "missing")
        return None, None
    except OSError as exc:
        _error(errors, rel, "unreadable", detail=type(exc).__name__)
        return None, None
    if raw.startswith(BOM):
        _error(errors, rel, "bom")
        raw = raw[len(BOM):]
    normalized = raw.replace(b"\r\n", b"\n")
    if len(raw) > 2 * MAX_BYTES or len(normalized) > MAX_BYTES:
        _error(errors, rel, "oversized", detail=str(len(normalized)))
        return None, None
    if b"\r" in normalized:
        _error(errors, rel, "lone_cr", normalized[:normalized.index(b"\r")].count(b"\n") + 1)
    try:
        text = normalized.decode("utf-8")
    except UnicodeDecodeError as exc:
        _error(errors, rel, "not_utf8", normalized[:exc.start].count(b"\n") + 1)
        return None, None
    for number, line in enumerate(text.split("\n"), start=1):
        if any(ord(char) > 127 for char in line):
            _error(errors, rel, "non_ascii", number)
    if not normalized.endswith(b"\n"):
        _error(errors, rel, "no_final_newline")
    return text, normalized


def _content(rel: str, text: str, errors: list) -> None:
    lines = text.split("\n")
    for number, line in enumerate(lines, start=1):
        for rule, pattern in FORBIDDEN:
            match = pattern.search(line)
            if match:
                _error(errors, rel, rule, number, match.group(0))
    present = set(lines)
    if rel == COMMON:
        if COMMON_MARKER not in present:
            _error(errors, rel, "marker_missing", detail=COMMON_MARKER)
        for section in COMMON_SECTIONS:
            if section not in present:
                _error(errors, rel, "section_missing", detail=section)
        for phrase in COMMON_PHRASES:
            if phrase not in text:
                _error(errors, rel, "phrase_missing", detail=phrase)
        return
    role = rel[len("roles/"):-len(".v1.md")]
    if not lines or lines[0] != "# Role contract v1: " + role:
        _error(errors, rel, "title_invalid", 1)
    if "contract: wd.bridge-role.v1 role=" + role not in present:
        _error(errors, rel, "marker_missing", detail="contract: wd.bridge-role.v1 role=" + role)
    for section in ROLE_SECTIONS:
        if section not in present:
            _error(errors, rel, "section_missing", detail=section)
    if COMMON not in text:
        _error(errors, rel, "common_reference_missing")


def _digest(files: Sequence[dict]) -> str:
    """One digest over ``<sha256> <path>`` lines in path order (sha256sum style)."""
    listing = "".join(entry["sha256"] + " " + entry["path"] + "\n" for entry in sorted(files, key=lambda e: e["path"]))
    return hashlib.sha256(listing.encode("ascii")).hexdigest()


def lint(directory: Path | str = DEFAULT_DIR) -> dict:
    """The lint report for one contracts directory. Raises OSError only when the directory itself is
    not a readable, unlinked directory."""
    directory = Path(directory)
    info = os.lstat(directory)
    reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if not stat.S_ISDIR(info.st_mode) or reparse:
        raise NotADirectoryError(str(directory))
    errors: list = []
    _inventory(directory, errors)
    files = []
    for rel in EXPECTED:
        text, normalized = _text(directory, rel, errors)
        if text is None:
            continue
        _content(rel, text, errors)
        files.append({"path": rel, "sha256": hashlib.sha256(normalized).hexdigest(), "bytes": len(normalized)})
    return {"schema": SCHEMA, "ok": not errors, "files": files, "source_digest": _digest(files) if files else None,
            "errors": errors}


def _utc_text(moment: Any) -> str:
    """Exactly a datetime; ONE offset read that is exactly a timedelta; subtraction, never astimezone."""
    if type(moment) is not datetime:
        raise ValueError("observed_utc must be exactly a datetime")
    offset = moment.utcoffset()
    if type(offset) is not timedelta:
        raise ValueError("observed_utc must be timezone-aware")
    return (moment.replace(tzinfo=None) - offset).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def role_receipts(report: Mapping[str, Any], assignments: Mapping[str, Sequence[str]], observed_utc: Any) -> list[dict]:
    """One receipt per worker for the roles it is EXPLICITLY assigned (module docstring)."""
    stamp = _utc_text(observed_utc)
    hashes = {entry["path"]: entry["sha256"] for entry in report.get("files", [])}
    broken = {error["path"] for error in report.get("errors", [])}
    receipts = []
    for worker in sorted(assignments):
        if not isinstance(worker, str) or WORKER.fullmatch(worker) is None:
            raise ValueError("worker id invalid")
        roles = sorted(set(assignments[worker]))
        errors = []
        paths = [COMMON] + ["roles/" + role + ".v1.md" for role in roles if role in ROLES]
        errors += ["unknown_role:" + role for role in roles if role not in ROLES]
        if not roles:
            errors.append("no_role_assigned")
        errors += ["contract_invalid:" + path for path in paths if path in broken or path not in hashes]
        contracts = {path: hashes[path] for path in paths if path in hashes}
        receipts.append({"schema": RECEIPT_SCHEMA, "worker": worker, "roles": roles, "observed_utc": stamp,
                         "verified": not errors, "errors": errors, "contracts": contracts,
                         "source_digest": _digest([{"path": p, "sha256": s} for p, s in contracts.items()])
                         if not errors else None})
    return receipts


def _assignment(text: str) -> tuple[str, list[str]]:
    worker, separator, roles = text.partition("=")
    if not separator or not roles:
        raise argparse.ArgumentTypeError("expected worker=role[,role]")
    return worker, [role for role in roles.split(",") if role]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint the bridge role contracts (read-only).")
    parser.add_argument("--contracts-dir", default=str(DEFAULT_DIR))
    parser.add_argument("--assign", action="append", type=_assignment, default=[],
                        help="worker=role[,role]: emit a role receipt for this explicit assignment")
    args = parser.parse_args(argv)
    try:
        report = lint(args.contracts_dir)
    except OSError as exc:
        print(json.dumps({"schema": SCHEMA, "ok": False, "error": type(exc).__name__}))
        return 2
    if args.assign:
        assignments: dict[str, list[str]] = {}
        for worker, roles in args.assign:
            assignments.setdefault(worker, []).extend(roles)
        try:
            report["receipts"] = role_receipts(report, assignments, datetime.now(timezone.utc))
        except ValueError as exc:
            print(json.dumps({"schema": SCHEMA, "ok": False, "error": str(exc)}))
            return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
