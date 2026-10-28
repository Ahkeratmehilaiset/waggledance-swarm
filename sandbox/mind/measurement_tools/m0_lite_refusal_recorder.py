#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Thin R2 command recorder, not a denial observer or an M0 launcher.

The caller validates its frozen graph and supplies approved source identities.
This module runs ONE supplied command, never discovers cases or changes pins.
Only local audit artifacts contain output; receipts contain hashes, not argv.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess

REQUIRED_PIN_NAMES = (
    "WD_BRIDGE_BUNDLE_ROOT", "WD_REBOOT_EXPECTED_MANIFEST_HASH",
    "WD_BRIDGE_CODE_ROOT", "WD_BRIDGE_BIN", "WD_BRIDGE_PYTHON_WRAPPER",
)
FILTER_MARKERS = (
    "PermissionError", "permission denied", "access is denied",
    "access denied", "operation not permitted", "refused",
)
LIMITATION = (
    "R2 limited evidence: exit/stderr screening does not prove absence of handled "
    "or unreported denials. Child-process internal refusals are not observed."
)
MAX_SCREEN_BYTES = 4 * 1024 * 1024
MAX_HOOK_BYTES = 1024 * 1024
_HEX64 = re.compile(r"[0-9a-fA-F]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def _utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":")).encode("ascii")


def _file_meta(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return {"path": str(path), "bytes": path.stat().st_size,
            "sha256": digest.hexdigest()}


def _source_identity():
    raw = Path(__file__).read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    git_blob = b"blob " + str(len(lf)).encode("ascii") + b"\0" + lf
    return {"sha256": _digest(raw),
            "git_blob_lf": hashlib.sha1(git_blob).hexdigest()}


def _pin_fingerprints():
    # Read only these non-secret launch pins, never enumerate the environment.
    return {name: _digest(value.encode("utf-8")) if value else None
            for name in REQUIRED_PIN_NAMES
            for value in (os.environ.get(name),)}


def _invoke(command, cwd, stdout, stderr, timeout):
    # env=None inherits the environment without copying/exposing secret values.
    return subprocess.run(command, cwd=cwd, stdout=stdout, stderr=stderr,
                          shell=False, check=False, timeout=timeout).returncode


def _is_pytest(command):
    exe = Path(command[0]).name.lower()
    if exe in ("pytest", "pytest.exe", "py.test", "py.test.exe"):
        return True
    return any(a == "-m" and b == "pytest"
               for a, b in zip(command[1:], command[2:])) or any(
                   Path(a).name.lower() in ("pytest.py", "pytest-script.py")
                   for a in command[1:] if not a.startswith("-"))


def _pytest_report(directory):
    tokens = shlex.split(os.environ.get("PYTEST_ADDOPTS", ""))
    if not any(a == "-p" and b == "hook_plugin_d15"
               for a, b in zip(tokens, tokens[1:])):
        raise ValueError("D15_PLUGIN_NOT_CONFIGURED")
    value = os.environ.get("WD_D15_HOOK_REPORT")
    if not value:
        raise ValueError("D15_REPORT_NOT_CONFIGURED")
    path = Path(value)
    if not path.is_absolute() or path.parent.resolve() != directory:
        raise ValueError("D15_REPORT_NOT_COMMAND_LOCAL")
    if path.exists() or path.is_symlink():
        raise ValueError("D15_REPORT_PREEXISTS")
    return path


def _read_hook(path, began_ns, ended_ns, exit_code):
    if not path.is_file() or path.is_symlink():
        raise ValueError("D15_REPORT_MISSING")
    stat = path.stat()
    if not began_ns <= stat.st_mtime_ns <= ended_ns:
        raise ValueError("D15_REPORT_STALE")
    if stat.st_size > MAX_HOOK_BYTES:
        raise ValueError("D15_REPORT_TOO_LARGE")
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]
    if not rows or any(type(row) is not dict for row in rows):
        raise ValueError("D15_REPORT_INVALID")
    summaries = [r for r in rows if r.get("kind") == "summary"]
    if len(summaries) != 1 or rows[-1] is not summaries[0]:
        raise ValueError("D15_SUMMARY_INVALID")
    summary = summaries[0]
    for key in ("refused", "subprocess", "exit"):
        if type(summary.get(key)) is not int or summary[key] < 0:
            raise ValueError("D15_COUNTER_INVALID")
    if summary["exit"] != exit_code:
        raise ValueError("D15_EXIT_MISMATCH")
    allowed = {"refused", "subprocess", "other", "failed", "collect_error", "summary"}
    if any(r.get("kind") not in allowed for r in rows):
        raise ValueError("D15_ROW_UNSUPPORTED")
    paths = sum(r.get("kind") == "refused" for r in rows)
    starts = [r for r in rows if r.get("kind") == "subprocess"]
    if any(type(r.get("refused")) is not bool for r in starts):
        raise ValueError("D15_SUBPROCESS_COUNTER_INVALID")
    if paths != summary["refused"] or len(starts) != summary["subprocess"]:
        raise ValueError("D15_COUNTER_MISMATCH")
    return {"status": "VERIFIED_D15_REPORT", "path": str(path),
            "sha256": _digest(raw),
            "freshness": {"basis": "exclusive command-local filesystem boundaries",
                          "before_ns": began_ns, "after_ns": ended_ns,
                          "report_mtime_ns": stat.st_mtime_ns},
            "refused": paths + sum(r["refused"] for r in starts),
            "path_refusals": paths, "subprocess_refusals": sum(r["refused"] for r in starts),
            "coverage": "pytest plugin process only; not child internals or all OS results"}


def record_command(*, attempt_id, stage, stage_number, command, cwd, audit_root,
                   phase0_sha256, graph_sha256, expected_source_sha256,
                   expected_source_blob, timeout_seconds=60):
    """Record one command; an existing attempt/stage directory is never reused.

    Graph/Phase0 hashes are caller-supplied lock references, not lock validation.
    git_blob_lf is computed with CRLF normalized to LF and compared to the
    caller-supplied committed blob; no Git lookup or authority is manufactured.
    The source SHA256 is the exact working/deployed file bytes, not normalized.
    """
    if not all(isinstance(s, str) and _IDENTIFIER.fullmatch(s)
               for s in (attempt_id, stage)):
        raise ValueError("INVALID_IDENTIFIER")
    if type(stage_number) is not int or not 1 <= stage_number <= 5:
        raise ValueError("INVALID_STAGE_NUMBER")
    if (not isinstance(command, (list, tuple)) or not command
            or any(type(word) is not str or "\0" in word for word in command)):
        raise ValueError("INVALID_COMMAND")
    if not Path(command[0]).is_absolute() or not Path(cwd).is_absolute():
        raise ValueError("ABSOLUTE_EXECUTOR_AND_CWD_REQUIRED")
    if (isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0):
        raise ValueError("FINITE_TIMEOUT_REQUIRED")
    root = Path(audit_root).resolve(strict=True)
    if not root.is_dir() or ".codex-audit" not in [p.lower() for p in root.parts]:
        raise ValueError("AUDIT_ROOT_REQUIRED")
    directory = root / (attempt_id + "--" + stage)
    directory.mkdir()  # exclusive reservation BEFORE any command can execute
    output, error, receipt = (directory / n for n in ("stdout.bin", "stderr.bin", "receipt.json"))
    command = list(command)
    result = {
        "schema": "wd.m0-lite.r2-command-receipt.v1",
        "attempt_id": attempt_id, "stage": stage, "stage_number": stage_number,
        "phase0_sha256": phase0_sha256, "graph_sha256": graph_sha256,
        "binding_authority": "caller validates frozen lock and installation separately",
        "command_sha256": _digest(_json_bytes(command)),
        "argv_sha256": _digest(_json_bytes(command[1:])),
        "cwd": str(cwd), "cwd_sha256": _digest(str(cwd).encode("utf-8")),
        "started_utc": None, "ended_utc": None, "exit_code": None,
        "recorder_started_utc": _utc(), "status": "INCOMPLETE",
        "evidence_class": "R2_LIMITED_EXIT_STDERR",
        "limited_indicator": None, "actual_denial_count": None,
        "child_process_internal_refusals": "NOT_OBSERVED", "limitation": LIMITATION,
        "hook": {"status": "UNKNOWN" if _is_pytest(command) else "NOT_APPLICABLE"},
        "errors": [], "receipt_path": str(receipt),
    }
    errors = result["errors"]
    hook_path = None
    begin_ns = end_ns = None
    with (directory / "intent.json").open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=True, sort_keys=True)
    try:
        result["filter_source"] = _source_identity()
        source = result["filter_source"]
        if (source["sha256"].lower() != str(expected_source_sha256).lower()
                or source["git_blob_lf"].lower() != str(expected_source_blob).lower()):
            errors.append("SOURCE_BINDING_MISMATCH")
        if not FILTER_MARKERS:
            errors.append("FILTER_UNAVAILABLE")
        if any(type(value) is not str or not _HEX64.fullmatch(value)
               for value in (phase0_sha256, graph_sha256)):
            errors.append("LOCK_REFERENCE_MISSING")
        pins = _pin_fingerprints()
        result["pin_fingerprints"] = pins
        if (set(pins) != set(REQUIRED_PIN_NAMES)
                or any(type(v) is not str or not _HEX64.fullmatch(v) for v in pins.values())):
            errors.append("PIN_MISSING")
        result["executable"] = _file_meta(Path(command[0]))
        cwd_stat = Path(cwd).stat()
        result["cwd_file_identity"] = {"device": cwd_stat.st_dev, "inode": cwd_stat.st_ino}
        if result["hook"]["status"] == "UNKNOWN":
            hook_path = _pytest_report(directory)
        with output.open("xb") as out, error.open("xb") as err:
            if not errors:
                result["started_utc"] = _utc()
                begin_ns = (directory / "intent.json").stat().st_mtime_ns if hook_path else None
                try:
                    result["exit_code"] = _invoke(command, str(cwd), out, err, timeout_seconds)
                finally:
                    if hook_path is not None:
                        # Same-filesystem bounds avoid mixing precise UTC with coarse file times.
                        boundary = directory / "hook-finished.stamp"
                        with boundary.open("xb"):
                            pass
                        end_ns = boundary.stat().st_mtime_ns
                    result["ended_utc"] = _utc()
        if _pin_fingerprints() != pins:
            errors.append("PINS_CHANGED")
        exit_code = result["exit_code"]
        if exit_code is not None:
            if type(exit_code) is not int or exit_code < 0 or exit_code >= 0xC0000000:
                errors.append("PROCESS_CRASHED")
            if error.stat().st_size > MAX_SCREEN_BYTES:
                errors.append("STDERR_SCREEN_LIMIT")
            else:
                try:
                    text = error.read_bytes().decode("utf-8")
                    result["stderr_marker_counts"] = {
                        marker: len(re.findall(re.escape(marker), text, re.IGNORECASE))
                        for marker in FILTER_MARKERS
                    }
                except UnicodeError:
                    errors.append("STDERR_UNDECODABLE")
            if hook_path is not None:
                result["hook"] = _read_hook(hook_path, begin_ns, end_ns, exit_code)
            if not errors:
                count = sum(result["stderr_marker_counts"].values())
                count += result["hook"].get("refused", 0)
                result["limited_indicator"] = max(count, int(exit_code != 0))
                result["status"] = "COMPLETED" if result["limited_indicator"] == 0 else "FAILED"
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        # Do not serialize exceptions: their messages can contain private argv.
        reason = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[A-Z0-9_]+", str(exc)) else type(exc).__name__
        errors.append(reason)
    finally:
        if errors:
            result["status"] = "INCOMPLETE"
            result["limited_indicator"] = None
        for name, path in (("stdout", output), ("stderr", error)):
            if not path.exists():
                with path.open("xb"):
                    pass
            result[name] = _file_meta(path)
        result["recorder_ended_utc"] = _utc()
        with receipt.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(result, handle, ensure_ascii=True, sort_keys=True, indent=2)
            handle.write("\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, help="local command metadata JSON, not case contents")
    args = parser.parse_args(argv)
    with Path(args.spec).open(encoding="utf-8") as handle:
        spec = json.load(handle)
    result = record_command(**spec)
    # No raw outputs, argv, pin values, scores or cases on stdout.
    path = Path(result["receipt_path"])
    print(json.dumps({"status": result["status"], "receipt": _file_meta(path),
                      "limited_indicator": result["limited_indicator"]}, sort_keys=True))
    return 0 if result["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())


