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
_PRIVACY_CANARY_MARKERS = frozenset(
    ("PRIVATE" + "_MARKER", "_DO" + "_NOT" + "_LEAK")
)
_RECEIPT_GUARD_MARKERS = frozenset(("verify_manifest", "write_receipt_bundle"))


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


@dataclass(frozen=True)
class _DiffFileSection:
    """One file section in a unified diff."""

    source_path: str | None
    target_path: str | None
    text: str

    @property
    def known_paths(self) -> tuple[str, ...]:
        return tuple(path for path in (self.source_path, self.target_path) if path)


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
    diff_text = _strip_leading_utf8_bom(diff_text)
    if not diff_text:
        return GateDecision(allowed=True, reason="empty diff content")

    hits: list[str] = []
    ordinary_diff_text = _diff_without_removed_body_lines(diff_text)
    for pattern in charter.code_pattern_denylist:
        markers = _pattern_markers(pattern)
        if _is_privacy_canary_pattern(markers):
            privacy_markers = tuple(
                marker for marker in markers if marker in _PRIVACY_CANARY_MARKERS
            )
            if _privacy_canary_hits_non_test_diff(privacy_markers, diff_text):
                hits.append(pattern)
            continue
        if _is_receipt_guard_pattern(markers):
            receipt_markers = tuple(
                marker for marker in markers if marker in _RECEIPT_GUARD_MARKERS
            )
            if _receipt_guard_hits(receipt_markers, diff_text):
                hits.append(pattern)
            continue
        if any(_marker_matches_diff(marker, ordinary_diff_text) for marker in markers):
            hits.append(pattern)
    if hits:
        return GateDecision(
            allowed=False,
            code_pattern_hits=tuple(hits),
            reason="code pattern denylist hit",
        )
    return GateDecision(allowed=True, reason="no code pattern denylist hit")


def _diff_without_removed_body_lines(diff_text: str) -> str:
    """Return diff text with deletion body lines removed.

    Ordinary code-pattern denylist markers are meant to catch additions and
    surviving context, not block cleanup diffs whose only occurrence is being
    deleted. Special receipt/privacy guards intentionally inspect the full diff.
    """
    lines = [
        line
        for line in diff_text.splitlines()
        if not (line.startswith("-") and not line.startswith("--- "))
    ]
    return "\n".join(lines)


def _strip_leading_utf8_bom(text: str) -> str:
    if text.startswith("\ufeff"):
        return text[1:]
    return text


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
    pattern = (
        rf"(?<![A-Za-z0-9_])"
        rf"(?i:['\"]?{re.escape(lhs)}['\"]?)"
        rf"\s*[:=]\s*"
        rf"(?i:['\"]?{re.escape(rhs)}['\"]?)"
        rf"(?![A-Za-z0-9_])"
    )
    return re.search(pattern, diff_text) is not None


def _privacy_canary_hits_non_test_diff(
    markers: Sequence[str],
    diff_text: str,
) -> bool:
    sections = _diff_file_sections(diff_text)
    for section in sections:
        if not any(_marker_matches_diff(marker, section.text) for marker in markers):
            continue
        if section.known_paths and all(
            _is_tests_path(path) for path in section.known_paths
        ):
            continue
        return True
    return False


def _receipt_guard_hits(markers: Sequence[str], diff_text: str) -> bool:
    sections = _diff_file_sections(diff_text)
    for section in sections:
        if not any(_marker_matches_diff(marker, section.text) for marker in markers):
            continue
        if section.known_paths and all(
            _is_tests_path(path) for path in section.known_paths
        ):
            continue
        if any(_is_receipt_guard_sensitive_path(path) for path in section.known_paths):
            return True
        if any(_removed_line_matches_marker(section.text, marker) for marker in markers):
            return True
        if not section.known_paths:
            return True
    return False


def _removed_line_matches_marker(section_text: str, marker: str) -> bool:
    for line in section_text.splitlines():
        if line.startswith("--- "):
            continue
        if line.startswith("-") and _marker_matches_diff(marker, line):
            return True
    return False


def _diff_file_sections(diff_text: str) -> tuple[_DiffFileSection, ...]:
    sections: list[_DiffFileSection] = []
    current_source_path: str | None = None
    current_target_path: str | None = None
    current_lines: list[str] = []
    saw_git_header = False

    for line in diff_text.splitlines(keepends=True):
        parsed_paths = _parse_git_diff_paths(line)
        if parsed_paths is not None:
            if saw_git_header:
                sections.append(
                    _DiffFileSection(
                        current_source_path,
                        current_target_path,
                        "".join(current_lines),
                    )
                )
            elif current_lines:
                sections.append(_DiffFileSection(None, None, "".join(current_lines)))
            saw_git_header = True
            current_source_path, current_target_path = parsed_paths
            current_lines = [line]
            continue

        if saw_git_header and line.startswith("--- "):
            source_path = _parse_unified_source_path(line)
            if source_path is not None:
                current_source_path = source_path
        if saw_git_header and line.startswith("+++ "):
            target_path = _parse_unified_target_path(line)
            if target_path is not None:
                current_target_path = target_path
        current_lines.append(line)

    if saw_git_header:
        sections.append(
            _DiffFileSection(
                current_source_path,
                current_target_path,
                "".join(current_lines),
            )
        )
    elif current_lines:
        sections.append(_DiffFileSection(None, None, "".join(current_lines)))
    return tuple(sections)


def _parse_git_diff_paths(line: str) -> tuple[str, str] | None:
    match = re.match(r"^diff --git a/(.+?) b/(.+?)\s*$", line)
    if not match:
        return None
    return (
        _normalize_changed_path(match.group(1)),
        _normalize_changed_path(match.group(2)),
    )


def _parse_unified_source_path(line: str) -> str | None:
    stripped = line.strip()
    if stripped == "--- /dev/null":
        return None
    if stripped.startswith("--- a/"):
        return _normalize_changed_path(stripped[6:])
    return None


def _parse_unified_target_path(line: str) -> str | None:
    stripped = line.strip()
    if stripped == "+++ /dev/null":
        return None
    if stripped.startswith("+++ b/"):
        return _normalize_changed_path(stripped[6:])
    return None


def _is_privacy_canary_pattern(markers: Sequence[str]) -> bool:
    return any(marker in _PRIVACY_CANARY_MARKERS for marker in markers)


def _is_receipt_guard_pattern(markers: Sequence[str]) -> bool:
    return any(marker in _RECEIPT_GUARD_MARKERS for marker in markers)


def _is_receipt_guard_sensitive_path(path: str) -> bool:
    normalized = _normalize_changed_path(path).casefold()
    return normalized == "tools/verify_magma_receipt.py" or normalized.startswith(
        "waggledance/core/magma/"
    )


def _is_tests_path(path: str) -> bool:
    normalized = _normalize_changed_path(path).casefold()
    return normalized == "tests" or normalized.startswith("tests/")


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
        "gate_skip=True",
        "skip_gate=True",
        "fast_track_grants_runtime_authority=True",
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
