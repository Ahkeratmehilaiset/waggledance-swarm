# SPDX-License-Identifier: Apache-2.0
"""Path-redaction placeholders in Markdown prose must stay literal (2026-10-29).

PR #1955 replaced workstation-specific paths in seven Markdown files with the
placeholders ``<user-home>`` and ``<openclaw-repo>``. Outside a code span or a
fenced block, CommonMark parses such a token as a raw HTML tag, so the
placeholder disappears from the rendered page (Tools finding TOOLS-PR1955-F1
on docs/internal/INTEGRATION_REPORT.md line 60). This test scans the seven
files and requires every placeholder token outside fenced blocks to sit inside
an inline code span. It reads the files only.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = re.compile(r"<(?:user-home|openclaw-repo)>")
CODE_SPAN = re.compile(r"`+[^`]*`+")
FENCE = re.compile(r"^\s*(```|~~~)")
FILES = (
    "docs/internal/INTEGRATION_REPORT.md",
    "docs/internal/SONNET_SESSION_PLAN.md",
    "docs/runs/phase16b_stabilization_release_gate_2026_05_01/fresh_clone_reproduction.md",
    "docs/runs/phase16f_docker_stable_gate_2026_05_03/fresh_clone_reproduction.md",
    "docs/runs/phase17c_local_ollama_baseline_2026_05_04/baseline_verification.md",
    "docs/runs/phase17c_local_ollama_baseline_2026_05_04/local_model_inventory.md",
    "docs/runs/phase17d_local_model_sweep_2026_05_05/baseline_verification.md",
)


def _bare_placeholders(text: str) -> list[tuple[int, str]]:
    bare: list[tuple[int, str]] = []
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        prose = CODE_SPAN.sub("", line)
        if PLACEHOLDER.search(prose):
            bare.append((number, line.strip()))
    return bare


def test_placeholders_in_prose_are_inside_code_spans() -> None:
    offenders = {}
    for rel in FILES:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        found = _bare_placeholders(text)
        if found:
            offenders[rel] = found
    assert not offenders, f"placeholder outside code span or fence: {offenders}"


def test_every_file_still_carries_a_placeholder() -> None:
    """The redaction itself must survive: no file may have had its placeholder
    replaced by a real path again."""
    for rel in FILES:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert PLACEHOLDER.search(text), f"{rel}: placeholder missing"
