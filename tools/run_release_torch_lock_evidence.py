#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify that the v3.12 torch dependency lock implements the signed decision.

This evidence is intentionally narrower than the release security/privacy gate:
it proves the lock follows the operator-authorized torch-family path. It never
marks a dependency audit clean; a fresh pip-audit/OSV artifact is still required.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import Specifier
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_release_torch_decision_evidence import (
    DEFAULT_TARGET_VERSION,
    implementation_authorization_from_decision_pack,
)


SCHEMA_VERSION = "waggledance.release_torch_lock_evidence.v1"
DEFAULT_REQUIREMENTS_LOCK = Path("requirements.lock.txt")
DEFAULT_OPERATOR_DECISION_PACK = Path("docs/operator_inbox/torch-cuda-vs-cpu.yaml")
DEFAULT_OUTPUT = (
    Path("docs")
    / "runs"
    / "release_soak_evidence"
    / "v3.12.0_torch_lock_evidence.json"
)

TORCH_FAMILY = ("torch", "torchvision", "torchaudio")
A2_CU126_WINDOWS_PINS = {
    "torch": "2.11.0+cu126",
    "torchvision": "0.26.0+cu126",
    "torchaudio": "2.11.0+cu126",
}
A2_CU126_NON_WINDOWS_PINS = {
    "torch": "2.11.0",
    "torchvision": "0.26.0",
    "torchaudio": "2.11.0",
}
A2_EXPECTED_TORCHAO = "0.17.0"
A2_EXPECTED_XFORMERS = "0.0.35"


def _format_utc(value: dt.datetime) -> str:
    normalized = value.astimezone(dt.UTC).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


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


def _line_entries(lock_path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    options: list[str] = []
    for lineno, raw in enumerate(lock_path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-"):
            options.append(stripped)
            continue
        try:
            requirement = Requirement(stripped)
        except InvalidRequirement:
            entries.append({
                "line": lineno,
                "raw": raw,
                "name": "",
                "version": "",
                "marker": "",
                "invalid": True,
            })
            continue
        entries.append({
            "line": lineno,
            "raw": raw,
            "name": canonicalize_name(requirement.name),
            "version": _exact_version(requirement),
            "marker": str(requirement.marker) if requirement.marker else "",
            "requirement": requirement,
            "invalid": False,
        })
    return entries, options


def _exact_version(requirement: Requirement) -> str:
    equals = [
        spec.version
        for spec in requirement.specifier
        if isinstance(spec, Specifier) and spec.operator == "=="
    ]
    return equals[0] if len(equals) == 1 else ""


def _applies_to_platform(requirement: Requirement, sys_platform: str) -> bool:
    if requirement.marker is None:
        return True
    env = default_environment()
    env["sys_platform"] = sys_platform
    return bool(requirement.marker.evaluate(env))


def _platform_pin(
    entries: list[dict[str, Any]],
    package: str,
    *,
    sys_platform: str,
) -> str:
    package_name = canonicalize_name(package)
    versions = []
    for entry in entries:
        requirement = entry.get("requirement")
        if entry.get("name") != package_name or not isinstance(requirement, Requirement):
            continue
        if _applies_to_platform(requirement, sys_platform):
            versions.append(str(entry.get("version") or ""))
    unique = sorted({version for version in versions if version})
    return unique[0] if len(unique) == 1 else ""


def _global_pin(entries: list[dict[str, Any]], package: str) -> str:
    package_name = canonicalize_name(package)
    versions = [
        str(entry.get("version") or "")
        for entry in entries
        if entry.get("name") == package_name
        and str(entry.get("version") or "")
        and not str(entry.get("marker") or "")
    ]
    unique = sorted(set(versions))
    return unique[0] if len(unique) == 1 else ""


def _stale_torch_lines(entries: list[dict[str, Any]]) -> list[str]:
    stale: list[str] = []
    names = {canonicalize_name(name) for name in TORCH_FAMILY}
    for entry in entries:
        if entry.get("name") not in names:
            continue
        raw = str(entry.get("raw") or "")
        version = str(entry.get("version") or "")
        if "+cu118" in raw or version.startswith("2.7.1"):
            stale.append(f"line {entry.get('line')}: {raw.strip()}")
    return stale


def _validate_a2_lock(
    entries: list[dict[str, Any]],
    options: list[str],
    authorization: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    expected_index = str(authorization.get("index_url") or "")
    index_present = any(expected_index in option for option in options)
    if not expected_index or not index_present:
        blockers.append("pytorch_cu126_extra_index_missing")

    windows_pins = {
        name: _platform_pin(entries, name, sys_platform="win32")
        for name in TORCH_FAMILY
    }
    linux_pins = {
        name: _platform_pin(entries, name, sys_platform="linux")
        for name in TORCH_FAMILY
    }
    darwin_pins = {
        name: _platform_pin(entries, name, sys_platform="darwin")
        for name in TORCH_FAMILY
    }
    if windows_pins != A2_CU126_WINDOWS_PINS:
        blockers.append("windows_cu126_pins_mismatch")
    if linux_pins != A2_CU126_NON_WINDOWS_PINS:
        blockers.append("linux_plain_pytorch_pins_mismatch")
    if darwin_pins != A2_CU126_NON_WINDOWS_PINS:
        blockers.append("darwin_plain_pytorch_pins_mismatch")

    torchao_pin = _global_pin(entries, "torchao")
    if torchao_pin != A2_EXPECTED_TORCHAO:
        blockers.append("torchao_compatibility_pin_missing")
    xformers_pin = _global_pin(entries, "xformers")
    if xformers_pin != A2_EXPECTED_XFORMERS:
        blockers.append("xformers_cu126_wheel_or_drop_unresolved")

    stale_lines = _stale_torch_lines(entries)
    if stale_lines:
        blockers.append("stale_cu118_or_torch_2_7_1_pins_present")

    return {
        "platform_strategy": "windows_cu126_linux_darwin_plain_pytorch",
        "pytorch_extra_index_url": expected_index,
        "pytorch_extra_index_present": index_present,
        "windows_pins": windows_pins,
        "linux_pins": linux_pins,
        "darwin_pins": darwin_pins,
        "torchao_pin": torchao_pin,
        "xformers_pin": xformers_pin,
        "stale_torch_lines": stale_lines,
    }, blockers


def build_report(
    *,
    commit: str,
    requirements_lock: Path,
    operator_decision_pack: Path,
    target_version: str = DEFAULT_TARGET_VERSION,
    generated_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    generated_at_utc = generated_at_utc or dt.datetime.now(dt.UTC)
    blockers: list[str] = []
    lock_summary: dict[str, Any] = {}
    authorization = implementation_authorization_from_decision_pack(
        operator_decision_pack,
        commit=commit,
        target_version=target_version,
    )
    if authorization is None:
        blockers.append("operator_decision_pack_unsigned_or_invalid")
    if not requirements_lock.exists():
        blockers.append("requirements_lock_missing")

    if not blockers:
        entries, options = _line_entries(requirements_lock)
        invalid_lines = [
            f"line {entry['line']}: {entry['raw']}"
            for entry in entries
            if entry.get("invalid")
        ]
        if invalid_lines:
            blockers.append("requirements_lock_invalid_lines")
            lock_summary["invalid_lines"] = invalid_lines
        elif authorization and authorization.get("chosen_option") == "A2_cu126":
            lock_summary, lock_blockers = _validate_a2_lock(
                entries,
                options,
                authorization,
            )
            blockers.extend(lock_blockers)
        else:
            blockers.append("unsupported_torch_decision_for_lock_evidence")

    status = "implemented" if not blockers else "blocked"
    return {
        "schema_version": SCHEMA_VERSION,
        "target_version": target_version,
        "commit": commit,
        "generated_at_utc": _format_utc(generated_at_utc),
        "requirements_lock": str(requirements_lock),
        "operator_decision_pack": str(operator_decision_pack),
        "torch_lock_status": status,
        "release_gate_effect": "none",
        "security_privacy_gate_status": "unchanged",
        "fresh_pip_audit_required": True,
        "pip_audit_skip_is_not_clean": True,
        "implementation_authorization": authorization,
        "lock_summary": lock_summary,
        "blockers": blockers,
    }


def evaluate_report(
    report: Mapping[str, Any],
    *,
    expected_commit: str | None = None,
    target_version: str = DEFAULT_TARGET_VERSION,
) -> list[str]:
    blockers: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        blockers.append("schema_version_invalid")
    if report.get("target_version") != target_version:
        blockers.append("target_version_mismatch")
    commit = report.get("commit")
    if not isinstance(commit, str) or not commit.strip():
        blockers.append("commit_missing")
    elif expected_commit is not None and commit != expected_commit:
        blockers.append("commit_mismatch")
    if report.get("release_gate_effect") != "none":
        blockers.append("release_gate_effect_must_be_none")
    if report.get("security_privacy_gate_status") != "unchanged":
        blockers.append("security_privacy_gate_must_stay_unchanged")
    if report.get("fresh_pip_audit_required") is not True:
        blockers.append("fresh_pip_audit_not_required")
    if report.get("pip_audit_skip_is_not_clean") is not True:
        blockers.append("pip_audit_skip_must_not_be_clean")
    report_blockers = report.get("blockers")
    if not isinstance(report_blockers, list):
        blockers.append("blockers_invalid")
    elif report_blockers:
        blockers.extend(str(item) for item in report_blockers)
    if report.get("torch_lock_status") != "implemented":
        blockers.append("torch_lock_not_implemented")
    auth = report.get("implementation_authorization")
    if not isinstance(auth, Mapping):
        blockers.append("implementation_authorization_missing")
    elif auth.get("chosen_option") != "A2_cu126":
        blockers.append("implementation_authorization_not_a2_cu126")
    return blockers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default="")
    parser.add_argument("--target-version", default=DEFAULT_TARGET_VERSION)
    parser.add_argument(
        "--requirements-lock",
        type=Path,
        default=DEFAULT_REQUIREMENTS_LOCK,
    )
    parser.add_argument(
        "--operator-decision-pack",
        type=Path,
        default=DEFAULT_OPERATOR_DECISION_PACK,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--allow-blocked",
        action="store_true",
        help="Exit 0 even when lock evidence remains blocked/fail-closed.",
    )
    args = parser.parse_args(argv)

    commit = args.commit or _current_commit()
    report = build_report(
        commit=commit,
        requirements_lock=args.requirements_lock,
        operator_decision_pack=args.operator_decision_pack,
        target_version=args.target_version,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    blockers = evaluate_report(report, expected_commit=commit)
    if not blockers or args.allow_blocked:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
