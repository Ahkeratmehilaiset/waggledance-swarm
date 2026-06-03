# SPDX-License-Identifier: BUSL-1.1
"""Idle Autonomy Charter loader and gate-check helpers.

The charter lives at ``docs/architecture/IDLE_AUTONOMY_CHARTER.md`` and defines
the file allowlist, file denylist, code-pattern denylist, and parallel
conditions that govern autonomous merge of idle-protocol consensus events.

This module parses the markdown charter into structured data and exposes
helpers that the upcoming ``tools/idle_consensus_to_pr.py`` will use to decide
whether a candidate diff is auto-mergeable.

The module is intentionally read-only with respect to the charter: it does not
modify, regenerate, or override the markdown source. Self-modification of the
charter remains banned by the charter itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import posixpath
import re
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHARTER_PATH = ROOT / "docs" / "architecture" / "IDLE_AUTONOMY_CHARTER.md"
DEFAULT_DAILY_QUOTA = 5


@dataclass(frozen=True)
class IdleAutonomyCharter:
    """Parsed Idle Autonomy Charter contents."""

    allowlist: tuple[str, ...]
    file_denylist: tuple[str, ...]
    code_pattern_denylist: tuple[str, ...]
    daily_quota: int
    operator_quotes: tuple[str, ...]


@dataclass(frozen=True)
class GateDecision:
    """Outcome of running a candidate diff through the charter gates."""

    allowed: bool
    blocked_paths: tuple[str, ...] = field(default_factory=tuple)
    code_pattern_hits: tuple[str, ...] = field(default_factory=tuple)
    unmatched_paths: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""


def load_charter(path: Path | None = None) -> IdleAutonomyCharter:
    """Parse the charter markdown into structured data.

    The parser tolerates minor markdown variations (heading hierarchy, bullet
    style) but expects the four canonical sections to be present.
    """
    target = path or DEFAULT_CHARTER_PATH
    if not target.exists():
        raise FileNotFoundError(f"charter not found: {target}")

    text = target.read_text(encoding="utf-8")
    sections = _split_sections(text)

    allowlist = tuple(_section_bullets(sections.get("Allowlist", "")))
    file_denylist = tuple(_section_bullets(sections.get("Denylist (file paths)", "")))
    code_pattern_denylist = tuple(
        _section_bullets(sections.get("Denylist (code patterns)", ""))
    )
    quotes = tuple(_extract_operator_quotes(text))

    return IdleAutonomyCharter(
        allowlist=allowlist,
        file_denylist=file_denylist,
        code_pattern_denylist=code_pattern_denylist,
        daily_quota=DEFAULT_DAILY_QUOTA,
        operator_quotes=quotes,
    )


def evaluate_paths(
    charter: IdleAutonomyCharter,
    changed_paths: Sequence[str],
) -> GateDecision:
    """Check a list of changed file paths against allowlist/denylist."""
    if not changed_paths:
        return GateDecision(
            allowed=False,
            reason="no changed paths provided",
        )

    blocked: list[str] = []
    unmatched: list[str] = []
    for changed in changed_paths:
        normalized = _normalize_changed_path(changed)
        if _matches_any(normalized, charter.file_denylist):
            blocked.append(normalized)
            continue
        if not _matches_any(normalized, charter.allowlist):
            unmatched.append(normalized)
    if blocked:
        return GateDecision(
            allowed=False,
            blocked_paths=tuple(blocked),
            reason="denylist hit",
        )
    if unmatched:
        return GateDecision(
            allowed=False,
            unmatched_paths=tuple(unmatched),
            reason="paths not on allowlist",
        )
    return GateDecision(allowed=True, reason="allowlist match, no denylist hit")


def evaluate_diff_content(
    charter: IdleAutonomyCharter,
    diff_text: str,
) -> GateDecision:
    """Check diff content against the code-pattern denylist.

    The patterns are matched as literal substrings within the diff body, with a
    small whitespace-tolerant path for exact ``identifier=value`` markers.
    """
    if not diff_text:
        return GateDecision(allowed=True, reason="empty diff content")

    hits: list[str] = []
    for pattern in charter.code_pattern_denylist:
        markers = _pattern_markers(pattern)
        if any(_marker_matches_diff(marker, diff_text) for marker in markers):
            hits.append(pattern)
    if hits:
        return GateDecision(
            allowed=False,
            code_pattern_hits=tuple(hits),
            reason="code pattern denylist hit",
        )
    return GateDecision(allowed=True, reason="no code pattern denylist hit")


def _split_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_title: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            if current_title is not None:
                sections[current_title] = "\n".join(buffer)
            current_title = match.group(1).strip()
            buffer = []
            continue
        if current_title is not None:
            buffer.append(line)
    if current_title is not None:
        sections[current_title] = "\n".join(buffer)
    return sections


def _section_bullets(section_text: str) -> list[str]:
    bullets: list[str] = []
    for line in section_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("* "):
            bullet = stripped[2:].strip()
            if bullet:
                bullets.extend(_bullet_values(bullet))
    return bullets


def _bullet_values(text: str) -> list[str]:
    backtick_values = [value.strip() for value in re.findall(r"`([^`]+)`", text)]
    if backtick_values:
        return [value for value in backtick_values if value]
    return [text]


def _matches_any(path: str, patterns: Sequence[str]) -> bool:
    for pattern in patterns:
        if _matches_glob(path, pattern):
            return True
    return False


def _matches_glob(path: str, pattern: str) -> bool:
    if not pattern:
        return False
    candidate = path.casefold()
    glob = pattern.casefold()
    if glob == candidate:
        return True
    if glob.endswith("/**"):
        prefix = glob[:-3]
        if candidate == prefix or candidate.startswith(prefix + "/"):
            return True
    if glob.endswith("/*"):
        prefix = glob[:-2]
        if candidate.startswith(prefix + "/") and "/" not in candidate[len(prefix) + 1 :]:
            return True
    if "*" in glob:
        regex = re.escape(glob).replace("\\*\\*", ".*").replace("\\*", "[^/]*")
        if re.fullmatch(regex, candidate):
            return True
    return False


def _marker_matches_diff(marker: str, diff_text: str) -> bool:
    if marker in diff_text:
        return True
    lhs, separator, rhs = marker.partition("=")
    if separator != "=":
        return False
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", lhs):
        return False
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", rhs):
        return False
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(lhs)}\s*=\s*(?i:{re.escape(rhs)})(?![A-Za-z0-9_])"
    return re.search(pattern, diff_text) is not None


def _normalize_changed_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    collapsed = posixpath.normpath(normalized)
    if collapsed == ".":
        return normalized
    return collapsed


def _pattern_markers(bullet: str) -> tuple[str, ...]:
    """Extract all probe substrings from a charter code-pattern bullet."""
    markers = list(re.findall(r"`([^`]+)`", bullet))
    for known_marker in (
        "auto_execute=False",
        "operator_gate_required=True",
        "DEFAULT_MAX_INSTANCES_PER_DAY",
        "_safe_label",
        "_sequence_errors",
        "verify_manifest",
        "write_receipt_bundle",
        "PRIVATE_MARKER",
        "_DO_NOT_LEAK",
    ):
        if known_marker in bullet and known_marker not in markers:
            markers.append(known_marker)
    return tuple(marker for marker in markers if marker)


def _extract_operator_quotes(text: str) -> list[str]:
    quotes: list[str] = []
    in_quote_section = False
    for line in text.splitlines():
        if line.startswith("## Operator Quotes"):
            in_quote_section = True
            continue
        if in_quote_section and line.startswith("## "):
            break
        if in_quote_section:
            match = re.search(r'"([^"]+)"', line)
            if match:
                quotes.append(match.group(1))
    return quotes
