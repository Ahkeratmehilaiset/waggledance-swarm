#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""No-blocking-human-prompt lint for EIG2 implementation-control text."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


FORBIDDEN_PHRASES = (
    "ask " + "human",
    "ask " + "user",
    "require " + "human review",
    "waiting " + "for human",
    "manual " + "approval required",
    "manual " + "confirmation required",
    "please " + "confirm",
    "human " + "must choose",
    "cannot " + "continue without approval",
    "wait " + "for operator",
    "prompt " + "user",
)

ALLOWED_CONTEXT_MARKERS = (
    "human_review_required",
    "operator runbook",
    "manual recovery documented",
    ".eig2.halt",
    ".eig2.autonomous_merge",
    "autonomous safety fence",
    "previously this would have said",
    "replaced with",
    "forbidden implementation-time phrases",
    "allowed phrases",
    "quoted historical references",
    "no-human prompt lint",
    "forbidden blocking phrases",
)

TEXT_SUFFIXES = {".md", ".py", ".json", ".jsonl", ".yaml", ".yml", ".txt"}


def _line_allowed(path: Path, lines: list[str], index: int) -> bool:
    if path.name == "no_human_prompt_lint.py":
        return True
    if path.name.lower() == "operator_runbook.md":
        return True

    window = "\n".join(lines[max(0, index - 2) : min(len(lines), index + 3)]).lower()
    return any(marker in window for marker in ALLOWED_CONTEXT_MARKERS)


def scan_text(text: str, path: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    lines = text.splitlines()
    for line_index, line in enumerate(lines):
        lowered = line.lower()
        for phrase in FORBIDDEN_PHRASES:
            pattern = r"\b" + re.escape(phrase) + r"\b"
            for match in re.finditer(pattern, lowered):
                if _line_allowed(path, lines, line_index):
                    continue
                findings.append(
                    {
                        "file": str(path),
                        "line": line_index + 1,
                        "phrase": phrase,
                        "match": match.group(0),
                        "context": line.strip(),
                    }
                )
    return findings


def scan_file(path: Path) -> list[dict[str, object]]:
    return scan_text(path.read_text(encoding="utf-8", errors="ignore"), path)


def iter_scan_paths(paths: list[str]) -> list[Path]:
    result: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_file():
            result.append(path)
        elif path.is_dir():
            for child in path.rglob("*"):
                if child.is_file() and child.suffix.lower() in TEXT_SUFFIXES:
                    result.append(child)
    return result


def main(paths: list[str]) -> int:
    all_findings: list[dict[str, object]] = []
    for path in iter_scan_paths(paths):
        all_findings.extend(scan_file(path))
    print(
        json.dumps(
            {
                "lint": "no_human_prompt_lint",
                "version": "eig2-v1.1",
                "total_findings": len(all_findings),
                "findings": all_findings,
            },
            indent=2,
        )
    )
    return 1 if all_findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
