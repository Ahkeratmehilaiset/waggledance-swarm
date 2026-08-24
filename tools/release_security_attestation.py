#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed attestation checks for release security/privacy artifacts.

Pure helpers used by tools/verify_release_soak_evidence.py when soak
evidence (actual or rebuilt-expected) claims ``profile_s_smoke`` or
``security_privacy_gate`` pass. They re-verify that the underlying local
artifacts genuinely support a *final* stable-evidence claim:

* the privacy precheck receipt must contain an exact stripped line
  matching ``<count> passed`` with the count at or above the historical
  floor of 74, plus the exact stripped line ``SMOKE_OK`` (substring
  echoes inside sentences never count), and must not carry explicit
  non-final / preliminary stable-evidence markers;
* the selected OSV/pip-audit report's canonical ``(name, version)`` pin
  multiset must equal the exact pins in ``requirements.lock.txt``.

All blockers are stable identifiers with no paths or artifact content.
No dependency resolution, network access, or audit re-run is performed.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRIVACY_REQUIRED_EXACT_LINES = ("SMOKE_OK",)
PRIVACY_PASSED_COUNT_FLOOR = 74
# The capture is bounded to nine digits: real suite counts fit easily,
# and a hostile arbitrarily-long digit string must fail the match (same
# stable blocker) instead of reaching int() and tripping Python's
# integer-digit limit with an exception.
_PRIVACY_PASSED_LINE_PATTERN = re.compile(r"^([1-9][0-9]{0,8}) passed$")
NON_FINAL_MARKERS = (
    "not final",
    "not-final",
    "non-final",
    "non final",
    "preliminary",
)
DEFAULT_REQUIREMENTS_LOCK = ROOT / "requirements.lock.txt"

_PIN_PATTERN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^;\s]+)")


def _canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _normalized_marker_text(text: str) -> str:
    """Normalize receipt text so Unicode variants cannot dodge markers.

    NFKC folds fullwidth/compatibility forms; format characters
    (category Cf, e.g. zero-width space) are removed; Unicode dash
    punctuation (category Pd) maps to an ASCII hyphen; whitespace runs
    (NBSP, tabs, doubled spaces, ...) collapse to one ASCII space.
    """
    text = unicodedata.normalize("NFKC", text)
    text = "".join(
        "-" if unicodedata.category(char) == "Pd" else char
        for char in text
        if unicodedata.category(char) != "Cf"
    )
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def evaluate_privacy_attestation(privacy_precheck: Path | str) -> list[str]:
    """Return stable blockers for the privacy precheck receipt.

    Empty list means the receipt attests a final pass: an exact
    stripped ``<count> passed`` line meets the historical floor, every
    other required token is present as an exact stripped line, and no
    explicit non-final / preliminary marker appears anywhere in the
    text. Missing, malformed, or under-floor passed lines all map to
    the same stable blocker.
    """
    try:
        text = Path(privacy_precheck).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ["privacy_attestation_unreadable"]

    blockers: list[str] = []
    stripped_lines = {line.strip() for line in text.splitlines()}
    passed_line_ok = False
    for line in stripped_lines:
        match = _PRIVACY_PASSED_LINE_PATTERN.match(line)
        if match and int(match.group(1)) >= PRIVACY_PASSED_COUNT_FLOOR:
            passed_line_ok = True
            break
    if not passed_line_ok or any(
        required not in stripped_lines
        for required in PRIVACY_REQUIRED_EXACT_LINES
    ):
        blockers.append("privacy_attestation_missing_exact_line")
    normalized = _normalized_marker_text(text)
    if any(marker in normalized for marker in NON_FINAL_MARKERS):
        blockers.append("privacy_attestation_not_final")
    return blockers


def _lock_pin_multiset(lock_path: Path) -> Counter | None:
    try:
        text = lock_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    pins: Counter = Counter()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        match = _PIN_PATTERN.match(line)
        if match:
            pins[(_canonical_name(match.group(1)), match.group(2))] += 1
    return pins


def _report_pin_multiset(report_path: Path) -> Counter | None:
    try:
        loaded = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, dict):
        return None
    dependencies = loaded.get("dependencies")
    if not isinstance(dependencies, list):
        return None
    pins: Counter = Counter()
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            return None
        name = dependency.get("name")
        version = dependency.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            return None
        pins[(_canonical_name(name), version)] += 1
    return pins


def evaluate_audited_lock_pins(
    audited_report: Path | str | None,
    requirements_lock: Path | str = DEFAULT_REQUIREMENTS_LOCK,
) -> list[str]:
    """Return stable blockers for OSV/pip-audit pin freshness.

    The audited report's canonical ``(name, version)`` multiset must
    equal the exact ``==`` pins of the requirements lock. A missing or
    malformed artifact on either side fails closed.
    """
    if audited_report is None:
        return ["audited_report_missing"]
    report_pins = _report_pin_multiset(Path(audited_report))
    if report_pins is None:
        return ["audited_report_unreadable"]
    lock_pins = _lock_pin_multiset(Path(requirements_lock))
    if lock_pins is None:
        return ["requirements_lock_unreadable"]
    if report_pins != lock_pins:
        return ["audited_lock_pins_stale"]
    return []
