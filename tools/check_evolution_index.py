"""Validator for iterations/EVOLUTION_INDEX.md.

Parses the YAML block under the `entries:` key in
iterations/EVOLUTION_INDEX.md and asserts every entry matches the
schema agreed in iterations/codex_scout_tasks/r20_synthesis_2026_05_09.md
(R20.1).

Run:

    python tools/check_evolution_index.py

Exits 0 on success, 1 on schema violation (with a human-readable error).
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = REPO_ROOT / "iterations" / "EVOLUTION_INDEX.md"


REQUIRED_FIELDS: dict[str, type | tuple[type, ...]] = {
    "session_id": str,
    "pr": (int, type(None)),
    "owner": str,
    "reviewer": (str, type(None)),
    # PyYAML auto-parses ISO-8601 timestamps to datetime, so accept
    # both the raw string and the parsed object.
    "merged_utc": (str, _dt.datetime, type(None)),
    "axis_a_before_ms": (int, float, type(None)),
    "axis_a_after_ms": (int, float, type(None)),
    "axis_a_metric": (str, type(None)),
    "axis_a_snapshot": (str, type(None)),
    "axis_b_quality": (int, float, type(None)),
    "axis_c_claim_to_push_minutes": (int, type(None)),
    "axis_c_push_to_merge_minutes": (int, type(None)),
    "runtime_behavior_changed": bool,
    "pre_merge_findings_caught": int,
    "post_merge_audit_findings": int,
    "failed_attempts": int,
    "lessons_learned": str,
    "next_bottleneck": str,
}

VALID_AGENTS = {"claude", "codex", "operator"}


_YAML_BLOCK_RE = re.compile(
    r"```yaml\s*\n(?P<body>entries:.*?)\n```",
    re.DOTALL,
)


def _extract_entries_yaml(markdown_text: str) -> str:
    """Pull the first ```yaml ... ``` block whose body starts with
    `entries:`. Raise ValueError if the file shape doesn't match."""
    match = _YAML_BLOCK_RE.search(markdown_text)
    if not match:
        raise ValueError(
            "EVOLUTION_INDEX.md is missing a ```yaml ... ``` block "
            "whose body begins with `entries:`"
        )
    return match.group("body")


def _validate_entry(entry: dict[str, Any], idx: int) -> list[str]:
    """Return a list of human-readable errors for one entry."""
    errors: list[str] = []
    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in entry:
            errors.append(f"entry[{idx}] missing required field {field!r}")
            continue
        value = entry[field]
        if not isinstance(value, expected_type):
            errors.append(
                f"entry[{idx}] field {field!r}: expected "
                f"{expected_type}, got {type(value).__name__} "
                f"(value={value!r})"
            )
    if entry.get("owner") not in VALID_AGENTS:
        errors.append(
            f"entry[{idx}] owner must be one of {sorted(VALID_AGENTS)}, "
            f"got {entry.get('owner')!r}"
        )
    reviewer = entry.get("reviewer")
    if reviewer is not None and reviewer not in VALID_AGENTS:
        errors.append(
            f"entry[{idx}] reviewer must be null or one of "
            f"{sorted(VALID_AGENTS)}, got {reviewer!r}"
        )
    # Sanity: if axis_a_before_ms is set, axis_a_after_ms and metric
    # should also be set (or the round has no measurable A-axis).
    before = entry.get("axis_a_before_ms")
    after = entry.get("axis_a_after_ms")
    metric = entry.get("axis_a_metric")
    if before is not None and after is None:
        errors.append(
            f"entry[{idx}] has axis_a_before_ms but no axis_a_after_ms"
        )
    if after is not None and before is None:
        errors.append(
            f"entry[{idx}] has axis_a_after_ms but no axis_a_before_ms"
        )
    if (before is not None or after is not None) and metric is None:
        errors.append(
            f"entry[{idx}] has axis_a numbers but no axis_a_metric"
        )
    return errors


def validate(index_path: Path = INDEX_PATH) -> tuple[bool, list[str]]:
    """Validate the index file. Return (ok, errors)."""
    if not index_path.is_file():
        return False, [f"EVOLUTION_INDEX.md not found at {index_path}"]
    text = index_path.read_text(encoding="utf-8")
    try:
        body = _extract_entries_yaml(text)
    except ValueError as exc:
        return False, [str(exc)]
    try:
        parsed = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        return False, [f"YAML parse error: {exc}"]
    if not isinstance(parsed, dict) or "entries" not in parsed:
        return False, [
            "parsed YAML must be a mapping with an `entries` list"
        ]
    entries = parsed["entries"]
    if not isinstance(entries, list):
        return False, ["entries must be a list"]
    errors: list[str] = []
    seen_session_ids: set[str] = set()
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"entry[{idx}] is not a mapping")
            continue
        errors.extend(_validate_entry(entry, idx))
        sid = entry.get("session_id")
        if isinstance(sid, str):
            if sid in seen_session_ids:
                errors.append(
                    f"entry[{idx}] duplicate session_id {sid!r}"
                )
            seen_session_ids.add(sid)
    return (not errors), errors


def main() -> int:
    ok, errors = validate()
    if ok:
        print(f"EVOLUTION_INDEX.md: schema OK")
        return 0
    print("EVOLUTION_INDEX.md: schema violations:", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
