# SPDX-License-Identifier: Apache-2.0
"""Tests for the D4 hex-cell competition result contract.

The v0 contract is evidence-only. It can name a deterministic winner and
losers for same-cell candidates, but it must not route traffic, mutate solver
candidate state, or deprecate losers without a later operator-gated promotion
path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from waggledance.core.solver_synthesis.hex_cell_competition import (
    HEX_CELL_COMPETITION_AUTHORITY_STATUS,
    HEX_CELL_COMPETITION_DIGEST_ALGORITHM,
    HEX_CELL_COMPETITION_RANKING_RULE,
    HEX_CELL_COMPETITION_RESULT_SCHEMA_VERSION,
    build_hex_cell_competition_result,
)
from waggledance.core.solver_synthesis.solver_candidate_store import (
    SolverCandidate,
)


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "solver_synthesis"
    / "hex_cell_competition_v0.json"
)


def _candidate(
    *,
    candidate_id: str = "cand-a",
    solver_name: str = "solver_a",
    cell_id: str = "thermal",
    capability_id: str = "frost-risk-detection",
    no_runtime_mutation: bool = True,
) -> SolverCandidate:
    return SolverCandidate(
        schema_version=1,
        candidate_id=candidate_id,
        state="shadow_only",
        solver_name=solver_name,
        cell_id=cell_id,
        spec_or_code={
            "kind": "threshold_rule",
            "capability_id": capability_id,
        },
        source_gap_ref="gap:d4-hex-cell-competition",
        no_runtime_mutation=no_runtime_mutation,
        produced_by="test",
        branch_name="test/d4",
        base_commit_hash="abc123",
        pinned_input_manifest_sha256="sha256:fixture",
        match_confidence=0.7,
    )


def _candidate_from_fixture(row: dict) -> SolverCandidate:
    return SolverCandidate(
        schema_version=1,
        candidate_id=row["candidate_id"],
        state="shadow_only",
        solver_name=row["solver_name"],
        cell_id=row["cell_id"],
        spec_or_code=dict(row["spec_or_code"]),
        source_gap_ref="gap:d4-hex-cell-competition",
        no_runtime_mutation=True,
        produced_by="test",
        branch_name="test/d4",
        base_commit_hash="abc123",
        pinned_input_manifest_sha256="sha256:fixture",
        match_confidence=0.7,
    )


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _build_from_fixture(payload: dict):
    candidates = [
        _candidate_from_fixture(row) for row in payload["candidates"]
    ]
    scores = {
        row["candidate_id"]: row["score"]
        for row in payload["candidates"]
    }
    evidence_refs = {
        row["candidate_id"]: row["evidence_refs"]
        for row in payload["candidates"]
    }
    result = build_hex_cell_competition_result(
        candidates=candidates,
        capability_id=payload["capability_id"],
        scores=scores,
        evidence_refs=evidence_refs,
    )
    return candidates, result


def test_fixture_builds_expected_non_authority_result():
    payload = _load_fixture()
    candidates, result = _build_from_fixture(payload)
    expected = payload["expected"]

    assert result.schema_version == (
        HEX_CELL_COMPETITION_RESULT_SCHEMA_VERSION
    )
    assert result.competition_id == expected["competition_id"]
    assert result.evidence_digest == expected["evidence_digest"]
    assert result.evidence_digest_algorithm == (
        HEX_CELL_COMPETITION_DIGEST_ALGORITHM
    )
    assert result.ranking_rule == HEX_CELL_COMPETITION_RANKING_RULE
    assert result.authority_status == HEX_CELL_COMPETITION_AUTHORITY_STATUS
    assert result.winner_id == expected["winner_id"]
    assert list(result.loser_ids) == expected["loser_ids"]

    assert result.runtime_traffic_mutation_applied is False
    assert result.candidate_state_mutation_applied is False
    assert result.operator_gate_required_for_authority is True
    assert [candidate.state for candidate in candidates] == [
        "shadow_only",
        "shadow_only",
        "shadow_only",
    ]


def test_digest_and_ranking_are_stable_when_input_order_changes():
    payload = _load_fixture()
    candidates, first = _build_from_fixture(payload)
    reversed_rows = list(reversed(payload["candidates"]))
    reversed_candidates = [
        _candidate_from_fixture(row) for row in reversed_rows
    ]

    second = build_hex_cell_competition_result(
        candidates=reversed_candidates,
        capability_id=payload["capability_id"],
        scores={
            row["candidate_id"]: row["score"]
            for row in reversed_rows
        },
        evidence_refs={
            row["candidate_id"]: row["evidence_refs"]
            for row in reversed_rows
        },
    )

    assert [candidate.candidate_id for candidate in candidates] == [
        "cand-alpha",
        "cand-beta",
        "cand-gamma",
    ]
    assert second.to_dict() == first.to_dict()


def test_tie_breaks_by_candidate_id_after_score_descending():
    result = build_hex_cell_competition_result(
        candidates=[
            _candidate(candidate_id="cand-z", solver_name="z"),
            _candidate(candidate_id="cand-a", solver_name="a"),
        ],
        capability_id="frost-risk-detection",
        scores={"cand-z": 0.9, "cand-a": 0.9},
    )

    assert result.winner_id == "cand-a"
    assert result.loser_ids == ("cand-z",)


def test_requires_at_least_two_candidates():
    with pytest.raises(ValueError, match="at least two"):
        build_hex_cell_competition_result(
            candidates=[_candidate()],
            capability_id="frost-risk-detection",
            scores={"cand-a": 0.8},
        )


def test_rejects_mixed_cell_ids():
    with pytest.raises(ValueError, match="share cell_id"):
        build_hex_cell_competition_result(
            candidates=[
                _candidate(candidate_id="cand-a", cell_id="thermal"),
                _candidate(candidate_id="cand-b", cell_id="energy"),
            ],
            capability_id="frost-risk-detection",
            scores={"cand-a": 0.8, "cand-b": 0.9},
        )


def test_rejects_mixed_capability_ids():
    with pytest.raises(ValueError, match="share capability_id"):
        build_hex_cell_competition_result(
            candidates=[
                _candidate(
                    candidate_id="cand-a",
                    capability_id="frost-risk-detection",
                ),
                _candidate(
                    candidate_id="cand-b",
                    capability_id="heat-pump-dispatch",
                ),
            ],
            capability_id="frost-risk-detection",
            scores={"cand-a": 0.8, "cand-b": 0.9},
        )


def test_rejects_missing_score():
    with pytest.raises(ValueError, match="missing scores"):
        build_hex_cell_competition_result(
            candidates=[
                _candidate(candidate_id="cand-a"),
                _candidate(candidate_id="cand-b"),
            ],
            capability_id="frost-risk-detection",
            scores={"cand-a": 0.8},
        )


def test_rejects_unknown_score_candidate():
    with pytest.raises(ValueError, match="unknown candidates"):
        build_hex_cell_competition_result(
            candidates=[
                _candidate(candidate_id="cand-a"),
                _candidate(candidate_id="cand-b"),
            ],
            capability_id="frost-risk-detection",
            scores={"cand-a": 0.8, "cand-b": 0.9, "cand-c": 0.7},
        )


def test_rejects_runtime_mutable_candidate():
    with pytest.raises(ValueError, match="no_runtime_mutation"):
        build_hex_cell_competition_result(
            candidates=[
                _candidate(candidate_id="cand-a"),
                _candidate(
                    candidate_id="cand-b",
                    no_runtime_mutation=False,
                ),
            ],
            capability_id="frost-risk-detection",
            scores={"cand-a": 0.8, "cand-b": 0.9},
        )


def test_rejects_non_finite_score():
    with pytest.raises(ValueError, match="score must be finite"):
        build_hex_cell_competition_result(
            candidates=[
                _candidate(candidate_id="cand-a"),
                _candidate(candidate_id="cand-b"),
            ],
            capability_id="frost-risk-detection",
            scores={"cand-a": 0.8, "cand-b": float("nan")},
        )
