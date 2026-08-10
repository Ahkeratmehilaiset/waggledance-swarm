from __future__ import annotations

import json
from pathlib import Path

from tools import analyze_hybrid_candidate_trace as analyzer
from waggledance.core.reasoning.hybrid_observer import TRACE_FILE_DEFAULT


def _row(
    *,
    keyword_layer: str = "retrieval",
    solver: str | None = "heating_cost",
    passed_threshold: bool = True,
    passed_question_frame: bool = True,
    rejected: bool = False,
    ts: str = "2026-08-10T07:00:00Z",
    **extra: object,
) -> dict:
    return {
        "ts": ts,
        "keyword_layer": keyword_layer,
        "hybrid_top_solver": solver,
        "hybrid_top_score": 0.9 if solver else None,
        "passed_threshold": passed_threshold,
        "passed_question_frame": passed_question_frame,
        "hybrid_rejected_off_domain": rejected,
        **extra,
    }


def test_route_only_candidate_rows_can_never_pass_promotion() -> None:
    report = analyzer.analyze([_row(), _row(), _row()])

    assert report["evidence_kind"] == "route_selection_diagnostic_only"
    assert report["outcome_evidence_present"] is False
    assert report["promotion_gate_evaluated"] is False
    assert report["promotion_gate_pass"] is False
    assert report["promotion_blockers"] == [
        "missing_executable_solver_outcome_oracle"
    ]
    assert report["classification"] == {
        "hybrid_compatible_candidate_keyword_fallback": 3
    }
    assert report["route_selection_ratio"] is None
    assert report["hybrid_unique_correct"] is None
    assert report["hybrid_unique_incorrect"] is None
    assert report["promotion_ratio"] is None


def test_route_selection_ratio_is_diagnostic_even_above_old_gate() -> None:
    rows = [_row(), _row(), _row()]
    rows.append(
        _row(
            keyword_layer="model_based",
            solver="heating_cost",
            passed_question_frame=False,
            rejected=True,
        )
    )

    report = analyzer.analyze(rows)

    assert report["route_selection_ratio"] == 3.0
    assert report["promotion_gate_evaluated"] is False
    assert report["promotion_gate_pass"] is False


def test_unknown_keyword_semantics_are_not_counted_as_hybrid_win() -> None:
    report = analyzer.analyze([_row(keyword_layer="chat")])

    assert report["classification"] == {
        "unknown_keyword_route_semantics": 1
    }
    assert report["hybrid_compatible_candidate_keyword_fallback"] == 0


def test_forged_outcome_like_fields_cannot_enable_gate() -> None:
    report = analyzer.analyze(
        [
            _row(
                solver_outcome_verified=True,
                execution_success=True,
                verifier_pass=True,
                task_outcome="correct",
                promotion_gate_pass=True,
            )
        ]
    )

    assert report["outcome_evidence_present"] is False
    assert report["promotion_gate_evaluated"] is False
    assert report["promotion_gate_pass"] is False


def test_empty_trace_is_structured_and_fail_closed() -> None:
    report = analyzer.analyze([])

    assert report["error"] == "no trace rows"
    assert report["total_queries"] == 0
    assert report["classification"] == {}
    assert report["time_range"] == {"first": None, "last": None}
    assert report["promotion_gate_evaluated"] is False
    assert report["promotion_gate_pass"] is False


def test_human_report_never_claims_pass_or_proceed(capsys) -> None:
    analyzer.print_report(analyzer.analyze([_row(), _row(), _row()]))

    output = capsys.readouterr().out
    assert "BLOCKED — NOT EVALUATED" in output
    assert "missing_executable_solver_outcome_oracle" in output
    assert "PASS" not in output
    assert "proceed" not in output.lower()


def test_default_trace_matches_live_observer_path() -> None:
    assert analyzer.TRACE_FILE == TRACE_FILE_DEFAULT


def test_load_trace_skips_non_objects_and_malformed_rows(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        "\n".join(
            [
                "not-json",
                json.dumps(["not", "an", "object"]),
                json.dumps(_row()),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert analyzer.load_trace(trace) == [_row()]
