# SPDX-License-Identifier: Apache-2.0
"""Tests for the Mama Event report renderers.

These tests pin two things:

* The renderers produce markdown that contains the honest data
  from the ablation matrix (not hard-coded stubs).
* No report text contains any consciousness/sentience/self-aware
  language, in English or Finnish.
"""

from __future__ import annotations

import pytest

from waggledance.observatory.mama_events.ablations import (
    AblationConfig,
    AblationMatrix,
    AblationRun,
    run_ablation_matrix,
)
from waggledance.observatory.mama_events.gate import GateVerdict
from waggledance.observatory.mama_events.reports import (
    CandidateRow,
    assert_no_hype,
    collect_candidate_rows,
    render_ablations_report,
    render_baseline_report,
    render_candidates_report,
    render_framework_report,
    render_gate_report,
)
from waggledance.observatory.mama_events.scoring import ScoreBand
from waggledance.observatory.mama_events.taxonomy import (
    EventType,
    MamaCandidateEvent,
)


# ── framework description ─────────────────────────────────


def test_framework_report_mentions_tiers_and_axes():
    text = render_framework_report()
    # tiers
    assert "T0" in text
    assert "T1" in text
    assert "T2" in text
    assert "T3" in text
    # axes A-F should all be called out
    for letter in "ABCDEF":
        assert f"**{letter}" in text or f"— {letter}" in text or f"{letter} —" in text


def test_framework_report_contains_no_hype():
    text = render_framework_report()
    assert_no_hype(text)


def test_framework_report_explicitly_denies_consciousness_claim():
    text = render_framework_report()
    # Make sure the disclaimer is actually present in some form.
    lowered = text.lower()
    assert "not" in lowered
    assert "detector" in lowered or "claim" in lowered


# ── baseline report ───────────────────────────────────────


def test_baseline_report_reflects_actual_run():
    matrix = run_ablation_matrix()
    text = render_baseline_report(matrix)

    assert f"`{matrix.baseline.verdict}`" in text
    assert f"Total events**: {matrix.baseline.event_count}" in text
    max_total = max(matrix.baseline.score_totals)
    assert f"Max score**: {max_total}" in text


def test_baseline_report_has_no_hype():
    matrix = run_ablation_matrix()
    text = render_baseline_report(matrix)
    assert_no_hype(text)


def test_baseline_report_lists_every_event_score():
    matrix = run_ablation_matrix()
    text = render_baseline_report(matrix)
    for total in matrix.baseline.score_totals:
        # every event row must be present in the table
        assert f"| {total} |" in text


# ── ablations report ──────────────────────────────────────


def test_ablations_report_lists_every_ablation():
    matrix = run_ablation_matrix()
    text = render_ablations_report(matrix)
    assert "`baseline`" in text
    for run in matrix.ablations:
        assert f"`{run.config.name}`" in text


def test_ablations_report_computes_delta_column():
    matrix = run_ablation_matrix()
    text = render_ablations_report(matrix)
    # at least one ablation must have a nonzero delta marker
    assert (" +" in text) or (" -" in text)


def test_ablations_report_has_no_hype():
    matrix = run_ablation_matrix()
    text = render_ablations_report(matrix)
    assert_no_hype(text)


def test_ablations_report_interprets_divergence():
    matrix = run_ablation_matrix()
    text = render_ablations_report(matrix)
    divergent = [
        r for r in matrix.ablations if r.verdict != matrix.baseline.verdict
    ]
    assert f"**{len(divergent)} of {len(matrix.ablations)}**" in text


# ── candidates list ───────────────────────────────────────


def test_collect_candidate_rows_sorts_by_score_desc():
    evts = [
        MamaCandidateEvent(
            event_type=EventType.LEXICAL,
            utterance_text="mama",
            speaker_id="a",
            timestamp_ms=i,
            session_id="s1",
        )
        for i in range(3)
    ]
    rows = collect_candidate_rows(
        events=evts,
        totals=[10, 80, 40],
        bands=["weak", "strong", "grounded"],
        contamination_flags=[(), (), ()],
    )
    assert [r.score for r in rows] == [80, 40, 10]
    assert rows[0].index == 1
    assert rows[1].index == 2
    assert rows[2].index == 0


def test_render_candidates_report_has_no_hype():
    rows = [
        CandidateRow(
            index=0,
            score=42,
            band=ScoreBand.CANDIDATE_GROUNDED.value,
            session_id="s1",
            caregiver="voice-user-1",
            contamination=(),
        )
    ]
    text = render_candidates_report(rows)
    assert "voice-user-1" in text
    assert "42" in text
    assert_no_hype(text)


def test_render_candidates_report_emits_table_header():
    text = render_candidates_report([])
    assert "| rank | idx | score | band | session | caregiver | contamination |" in text


# ── gate report ───────────────────────────────────────────


def test_gate_report_reflects_baseline_verdict():
    matrix = run_ablation_matrix()
    text = render_gate_report(matrix)
    assert f"`{matrix.baseline.verdict}`" in text
    # should mention every verdict in the closed set
    for v in GateVerdict:
        assert v.value in text


def test_gate_report_has_no_hype():
    matrix = run_ablation_matrix()
    text = render_gate_report(matrix)
    assert_no_hype(text)


def test_gate_report_includes_honesty_notes():
    matrix = run_ablation_matrix()
    text = render_gate_report(matrix)
    low = text.lower()
    assert "deterministic" in low
    assert "ablation" in low


# ── invariant assertion helper ────────────────────────────


def test_assert_no_hype_accepts_clean_text():
    assert_no_hype("This is a proto-social candidate event report.")


def test_assert_no_hype_rejects_english_hype():
    with pytest.raises(AssertionError):
        assert_no_hype("the system demonstrates conscious behavior")


def test_assert_no_hype_rejects_finnish_hype():
    with pytest.raises(AssertionError):
        assert_no_hype("järjestelmä on tietoinen itsestään")


def test_assert_no_hype_rejects_all_forbidden_terms():
    for bad in ("self-aware", "selfaware", "sentient", "SENTIENT"):
        with pytest.raises(AssertionError):
            assert_no_hype(f"really {bad} agent")
