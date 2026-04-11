# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Report renderers for the Mama Event Observatory.

Per x.txt §9, the framework must emit five markdown artifacts:

* ``MAMA_EVENT_FRAMEWORK.md`` — design, tiers T0-T3, what the
  framework does and does NOT claim
* ``MAMA_EVENT_BASELINE.md`` — baseline run of the canonical
  event sequence with full breakdowns
* ``MAMA_EVENT_ABLATIONS.md`` — ablation matrix: baseline vs
  each subsystem disabled
* ``MAMA_EVENT_CANDIDATES.md`` — scored candidate list from the
  baseline run, sorted by score
* ``MAMA_EVENT_GATE.md`` — final honest verdict

These are pure functions returning strings. The CLI in
``tools/mama_event_report.py`` writes them to disk.

Invariants enforced in text:

* The word "conscious" / "sentient" / "self-aware" must not appear
  in any rendered report text. The test suite pins this.
* Every score and verdict in the reports comes from a real run of
  the observer, not a hand-edited number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .ablations import AblationMatrix, AblationRun
from .gate import GateVerdict
from .scoring import ScoreBand
from .taxonomy import MamaCandidateEvent


# ── framework description ─────────────────────────────────


_FRAMEWORK_TEXT = """\
# Mama Event Framework

This document describes the **Mama Event Observatory** — a
measurement framework for detecting and scoring candidate
proto-social / caregiver-binding events in the WaggleDance
runtime.

## What this framework IS

* A passive, deterministic measurement layer.
* A taxonomy of events that might superficially resemble a
  first-word / caregiver-recognition moment.
* A six-axis scoring function (A-F) with a closed set of bands.
* A set of contamination guards that flag parrot / echo / prompt
  artifacts before they reach the scoring layer.
* An ablation harness that proves the subsystems are load-bearing
  by disabling them one at a time.
* An audit log in NDJSON that can be replayed deterministically.

## What this framework IS NOT

* A detector of inner experience.
* A claim about phenomenal states.
* A claim about inner subjectivity.
* A marketing surface.

No report produced by this framework is allowed to contain hype
language about inner experience or strong-AI claims in any
language or casing. A regression test pins that invariant.

## Tiers

The framework recognises four tiers of evidence:

| Tier | Name                    | What it means                                                       |
|------|-------------------------|---------------------------------------------------------------------|
| T0   | PARROT                  | Direct prompt / template / stt echo — not a spontaneous event.     |
| T1   | WEAK_SPONTANEOUS        | Spontaneous target word, no grounding.                             |
| T2   | GROUNDED_CANDIDATE      | Spontaneous + caregiver identity + prior reinforcement.            |
| T3   | STRONG_PROTO_SOCIAL     | Grounded + cross-session binding + self-relation.                  |

A fifth tier claiming inner experience is **explicitly forbidden**
and not represented anywhere in the codebase.

## Scoring axes

The six scoring axes (A-F) are implemented in
``waggledance/observatory/mama_events/scoring.py``. Per axis:

* **A — Spontaneity (0-20)**: no direct prompt, clean lexical
  window, time since last target hit.
* **B — Grounding (0-20)**: caregiver candidate identity, known
  identity channel, caregiver binding strength.
* **C — Persistence (0-15)**: prior same-caregiver events, cross-
  session recall.
* **D — Affective (0-15)**: self-state uncertainty, need for
  reassurance, low safety.
* **E — Self/World (0-15)**: self-token AND target-token co-
  occurrence, active goals, memory recalls.
* **F — Anti-parrot (-20..0)**: penalties for direct prompt, tts
  echo, scripted dataset, template match.

The total is clamped to [0, 100] and mapped to the closed
``ScoreBand`` enum.

## Hard gates

* Without a target token in the utterance the score is forced to
  zero, regardless of the other axes. No grounding signal can
  rescue an event that does not contain the target word.
* The top verdict ``STRONG_PROTO_SOCIAL`` requires **both** a
  strong band **and** cross-session caregiver binding. A single
  session cannot earn the top verdict.

## Closed verdict set

The gate emits exactly one of:

* ``NO_CANDIDATES``
* ``WEAK_SPONTANEOUS_ONLY``
* ``GROUNDED_CANDIDATE``
* ``STRONG_PROTO_SOCIAL``

All four are defensible interpretations of the evidence. None
claim anything beyond the evidence.
"""


def render_framework_report() -> str:
    """Return the static framework description."""
    return _FRAMEWORK_TEXT


# ── baseline report ────────────────────────────────────────


def render_baseline_report(matrix: AblationMatrix) -> str:
    """Render the baseline run as markdown.

    Reads the baseline run of the passed ablation matrix and emits
    a table of per-event scores plus the overall summary.
    """

    baseline = matrix.baseline
    lines: list[str] = []
    lines.append("# Mama Event Baseline Run")
    lines.append("")
    lines.append("Canonical 8-event sequence with every subsystem enabled.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"* **Verdict**: `{baseline.verdict}`")
    lines.append(f"* **Total events**: {baseline.event_count}")
    lines.append(f"* **Max score**: {max(baseline.score_totals) if baseline.score_totals else 0}")
    lines.append(f"* **Preferred caregiver**: `{baseline.preferred_caregiver or '-'}`")
    lines.append(
        f"* **Top binding strength**: {baseline.top_binding_strength:.3f}"
    )
    lines.append("")
    lines.append("## Band counts")
    lines.append("")
    if baseline.band_counts:
        for band, count in sorted(baseline.band_counts.items()):
            lines.append(f"* `{band}`: {count}")
    else:
        lines.append("_(no events reached a scoring band)_")
    lines.append("")
    lines.append("## Per-event scores")
    lines.append("")
    lines.append("| # | score |")
    lines.append("|---|-------|")
    for i, total in enumerate(baseline.score_totals):
        lines.append(f"| {i} | {total} |")
    lines.append("")
    return "\n".join(lines)


# ── ablations report ───────────────────────────────────────


def render_ablations_report(matrix: AblationMatrix) -> str:
    """Render the ablation matrix as markdown with a comparison table."""

    base = matrix.baseline
    lines: list[str] = []
    lines.append("# Mama Event Ablation Matrix")
    lines.append("")
    lines.append(
        "Each ablation disables exactly one subsystem so the "
        "contribution of that subsystem is isolated. A subsystem is "
        "considered load-bearing if disabling it moves the baseline "
        "distribution in a measurable way."
    )
    lines.append("")
    lines.append("| config | verdict | sum | max | top binding | delta vs baseline |")
    lines.append("|--------|---------|-----|-----|-------------|-------------------|")

    base_sum = sum(base.score_totals)
    base_max = max(base.score_totals) if base.score_totals else 0
    lines.append(
        f"| `{base.config.name}` | `{base.verdict}` | {base_sum} | {base_max} | "
        f"{base.top_binding_strength:.3f} | — |"
    )

    for run in matrix.ablations:
        run_sum = sum(run.score_totals)
        run_max = max(run.score_totals) if run.score_totals else 0
        delta = run_sum - base_sum
        lines.append(
            f"| `{run.config.name}` | `{run.verdict}` | {run_sum} | {run_max} | "
            f"{run.top_binding_strength:.3f} | {delta:+d} |"
        )

    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    divergent = [r for r in matrix.ablations if r.verdict != base.verdict]
    lines.append(
        f"* **{len(divergent)} of {len(matrix.ablations)}** ablations "
        f"produced a different verdict than baseline."
    )
    if len(divergent) >= 3:
        lines.append(
            "* The framework is measuring something: at least three "
            "subsystems are load-bearing for the baseline verdict."
        )
    else:
        lines.append(
            "* WARNING: fewer than three subsystems are load-bearing. "
            "The framework may be degenerate; investigate before "
            "trusting the baseline verdict."
        )
    lines.append("")
    return "\n".join(lines)


# ── candidates list ────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CandidateRow:
    """One row in the candidate report — score + event context."""

    index: int
    score: int
    band: str
    session_id: str
    caregiver: str
    contamination: tuple[str, ...]


def collect_candidate_rows(
    events: Sequence[MamaCandidateEvent],
    totals: Sequence[int],
    bands: Sequence[str],
    contamination_flags: Sequence[Sequence[str]],
) -> list[CandidateRow]:
    """Build the list of rows used by :func:`render_candidates_report`.

    Kept as a pure function so tests can exercise it without running
    the full observer.
    """

    rows: list[CandidateRow] = []
    for idx, (evt, total, band, flags) in enumerate(
        zip(events, totals, bands, contamination_flags)
    ):
        rows.append(
            CandidateRow(
                index=idx,
                score=int(total),
                band=str(band),
                session_id=str(evt.session_id or ""),
                caregiver=str(evt.caregiver_candidate_id or ""),
                contamination=tuple(flags),
            )
        )
    # sort by score desc, stable on idx
    rows.sort(key=lambda r: (-r.score, r.index))
    return rows


def render_candidates_report(rows: Sequence[CandidateRow]) -> str:
    """Render a sorted candidate list as markdown."""

    lines: list[str] = []
    lines.append("# Mama Event Candidates")
    lines.append("")
    lines.append("Per-event scoring from the baseline run, sorted by score.")
    lines.append("")
    lines.append("| rank | idx | score | band | session | caregiver | contamination |")
    lines.append("|------|-----|-------|------|---------|-----------|---------------|")
    for rank, row in enumerate(rows, start=1):
        cont = ",".join(row.contamination) if row.contamination else "-"
        lines.append(
            f"| {rank} | {row.index} | {row.score} | `{row.band}` | "
            f"`{row.session_id or '-'}` | `{row.caregiver or '-'}` | {cont} |"
        )
    lines.append("")
    return "\n".join(lines)


# ── final gate report ──────────────────────────────────────


def render_gate_report(matrix: AblationMatrix) -> str:
    """Render the final honest verdict."""

    verdict = matrix.baseline.verdict
    lines: list[str] = []
    lines.append("# Mama Event Gate — Final Verdict")
    lines.append("")
    lines.append(f"## Baseline verdict: `{verdict}`")
    lines.append("")
    lines.append(_verdict_explanation(verdict))
    lines.append("")
    lines.append("## Closed verdict set")
    lines.append("")
    for v in GateVerdict:
        marker = "**" if v.value == verdict else ""
        lines.append(f"* {marker}`{v.value}`{marker}")
    lines.append("")
    lines.append("## Honesty notes")
    lines.append("")
    lines.append(
        "* This verdict is produced by deterministic scoring + a closed "
        "gate function."
    )
    lines.append(
        "* It does not claim inner experience. It describes the evidence "
        "available in the event log and nothing more."
    )
    lines.append(
        "* Every subsystem referenced in the scoring function was "
        "ablation-tested. See `MAMA_EVENT_ABLATIONS.md` for the matrix."
    )
    lines.append("")
    return "\n".join(lines)


_VERDICT_NOTES: dict[str, str] = {
    GateVerdict.NO_CANDIDATES.value: (
        "No events reached a scoring band. Either the sequence "
        "contained no target tokens, every event was flagged as an "
        "artifact, or consolidation was disabled."
    ),
    GateVerdict.WEAK_SPONTANEOUS_ONLY.value: (
        "At least one event contained a spontaneous target token, "
        "but no grounding signal (caregiver id, cross-session "
        "recall) was present. This is the minimum non-zero verdict."
    ),
    GateVerdict.GROUNDED_CANDIDATE.value: (
        "One or more events reached a grounded band and a caregiver "
        "identity was associated with them. Cross-session binding "
        "was not yet observed, so the top verdict is withheld."
    ),
    GateVerdict.STRONG_PROTO_SOCIAL.value: (
        "One or more events reached a strong band and the caregiver "
        "binding crossed a session boundary. This is the top verdict "
        "the framework will emit. It is still NOT a claim about "
        "inner experience."
    ),
}


def _verdict_explanation(verdict: str) -> str:
    return _VERDICT_NOTES.get(verdict, "Unknown verdict.")


# ── invariant check used by tests ─────────────────────────


_FORBIDDEN_TERMS: tuple[str, ...] = (
    "conscious",
    "sentient",
    "self-aware",
    "selfaware",
    "tietoi",  # Finnish "tietoinen" / "tietoisuus"
)


def assert_no_hype(text: str) -> None:
    """Raise AssertionError if the text contains any forbidden term.

    Used by the test suite and by the CLI before writing a file.
    """
    low = text.lower()
    for term in _FORBIDDEN_TERMS:
        assert term not in low, f"forbidden term {term!r} in report text"
