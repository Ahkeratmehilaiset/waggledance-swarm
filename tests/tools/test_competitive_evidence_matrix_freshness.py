# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs" / "benchmarks" / "COMPETITIVE_EVIDENCE_MATRIX_2026.md"


EXPECTED_AXIS_LABELS = {
    "A": "**PROVEN.**",
    "B": "**PROVEN.**",
    "C": "**PROVEN.**",
    "D": "**PROVEN** for the Phase 11-16F runtime stack. **PROVEN this session** for Phase 17A producer fabric and 10k scale.",
    "E": "**PROVEN.**",
    "F": "**PROVEN this session.**",
    "G": "**MEASURED this session.** **PROVEN** that the data path works (no FIFO fallback, no miss, capability-lookup-only). The 10,000 number itself is a measured ceiling, not an architectural maximum.",
    "H": "**PROVEN.**",
    "I": "**NOT CLAIMED.**",
    "J": '**INFERRED** for the architecture; **MEASURED-LOCAL-OLLAMA-PANEL** for the 4-model panel latency this session. The hybrid accuracy delta was not measured this session. **No cross-vendor ranking is implied** - every per-model number is reported in isolation; the harness\'s MD scrub blocks "is faster than" / "outperforms" / "beats" / "better than" / "ranks higher" substrings from the rendered prose.',
    "K": "**INFERRED.** The architecture supports the claim; no production deployment data has been published.",
    "L": "**MEASURED** image size; **INFERRED** edge fitness.",
    "M": "**PROVEN with persisted, idempotent, cursor-incremental runtime-gap replay, RuntimeGapDetector bridge, measured feedback loop, and runtime dispatch of mined solver specs within six-family allowlist** (Phase 18F). **NOT CLAIMED** for high-risk families. Builder-handoff lane is **PROVEN as a quarantined contract**, **NOT CLAIMED as automatic builder promotion**.",
    "N": "**PROVEN** as a refusal contract; **NOT CLAIMED** that all conceivable risk modes are catalogued.",
    "O": "**PROVEN this session.**",
}


def _read_matrix() -> str:
    return MATRIX.read_text(encoding="utf-8")


def _canonical_label(label: str) -> str:
    return label.replace("\u2013", "-").replace("\u2014", "-")


def _axis_labels(text: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    current_axis = ""
    for line in text.splitlines():
        axis_match = re.match(r"^### ([A-O])\. ", line)
        if axis_match:
            current_axis = axis_match.group(1)
            continue
        if current_axis and line.startswith("* **Label:** "):
            labels[current_axis] = _canonical_label(
                line.removeprefix("* **Label:** ").strip()
            )
            current_axis = ""
    return labels


def test_freshness_note_marks_20260606_staleness_without_label_upgrade() -> None:
    text = _read_matrix()

    assert "**Evidence snapshot date:** 2026-05-06" in text
    assert "2026-06-06 read-only audit" in text
    assert "following the 2026-05-27 read-only audit" in text
    assert "`freshness_audit_date=2026-06-06`" in text
    assert "31-33 days old" in text
    assert "staleness marker only, not a rerun" in text
    assert "label upgrade" in text
    assert "row invalidation" in text


def test_current_freshness_audit_preserves_axis_labels() -> None:
    labels = _axis_labels(_read_matrix())

    assert labels == EXPECTED_AXIS_LABELS
