#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Machine-derived MAGMA event surface scan for v3.13.0.

Scans waggledance/core/v3_13_0/ and tools/sim_orchestrator.py for
every literal MAGMA event_type value emitted, including:
* explicit "event_type": "..." dict-literal values in emit_magma_event
  payloads;
* Enum member values (e.g. AuditEventType.INTENT_CLASSIFIED = "write.
  intent_classified");
* audit_event_type="..." keyword arguments (used by AntiPatternCatalog
  InvariantViolation entries);
* positional self._audit("...", ...) string arguments;
* nested emit_magma_event({ ..., "event_type": "..." }) literals.

Run as a release-prep evidence tool. Counts and the full list are
quoted in docs/releases/v3.13.0.md and docs/release/RELEASE_READINESS.md;
this script is the single source of that truth so the docs cannot
silently drift.

Usage:
    python tools/audit_v3_13_0_event_surface.py
    python tools/audit_v3_13_0_event_surface.py --count-only
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys


REPO = pathlib.Path(__file__).resolve().parent.parent
MODULE_DIR = REPO / "waggledance" / "core" / "v3_13_0"
SIM_PATH = REPO / "tools" / "sim_orchestrator.py"

PATTERNS = [
    re.compile(r'"event_type":\s*"([a-z_]+\.[a-z_]+)"'),
    re.compile(
        r'^\s*[A-Z_]+\s*=\s*"([a-z_]+\.[a-z_]+)"',
        re.MULTILINE,
    ),
    re.compile(r'audit_event_type\s*=\s*"([a-z_]+\.[a-z_]+)"'),
    re.compile(r'self\._audit\(\s*"([a-z_]+\.[a-z_]+)"'),
    re.compile(
        r'\.emit_magma_event\(\{[^}]*?"event_type":\s*"([a-z_]+\.[a-z_]+)"',
        re.DOTALL,
    ),
]


def collect_event_types() -> set[str]:
    found: set[str] = set()
    sources = list(MODULE_DIR.glob("*.py"))
    if SIM_PATH.exists():
        sources.append(SIM_PATH)
    for path in sources:
        content = path.read_text(encoding="utf-8")
        for pat in PATTERNS:
            for match in pat.finditer(content):
                found.add(match.group(1))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count-only",
        action="store_true",
        help="Print only the integer count.",
    )
    args = parser.parse_args(argv)

    events = collect_event_types()
    if args.count_only:
        print(len(events))
        return 0

    print(f"v3.13.0 MAGMA event surface: {len(events)} unique event types")
    print()
    by_prefix: dict[str, list[str]] = {}
    for et in sorted(events):
        prefix = et.split(".", 1)[0]
        by_prefix.setdefault(prefix, []).append(et)
    for prefix in sorted(by_prefix):
        items = by_prefix[prefix]
        print(f"  {prefix}.* ({len(items)}):")
        for et in items:
            print(f"    - {et}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
