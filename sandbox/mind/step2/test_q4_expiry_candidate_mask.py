"""Candidate-time validity for public cards, using a model-free synthetic index."""

import pytest

from sandbox.mind.step2 import q4
from sandbox.mind.step2.test_q4_readonly_port import FakeEncoder, fake_memory


def engine_with_expiries(expiries):
    memory = fake_memory(records=len(expiries))
    for row in memory.views["learning"].rows:
        index = int(row["provenance"]["record"].split("-")[-1])
        expiry = expiries[index]
        if expiry != "legacy":
            row["provenance"]["source_kind"] = "shared_closed_card"
            if expiry != "missing":
                row["provenance"]["valid_until_utc"] = expiry
    return q4.Q4(memory, FakeEncoder())


def test_expired_only_is_abstained_before_ranking():
    engine = engine_with_expiries(["2026-01-02T00:00:00Z"] * 5)
    result = engine.ask("retry network", "2026-01-02T00:00:00Z")
    assert result["status"] == "ABSTAIN"
    assert result["results"] == []
    assert result["telemetry"]["excluded_expired"] == 5


def test_mixed_candidates_exclude_expired_before_consensus():
    engine = engine_with_expiries(["2026-01-01T23:59:00Z", "2026-01-02T00:00:00Z"]
                                  + ["2026-01-03T00:00:00Z"] * 4)
    result = engine.ask("retry network", "2026-01-02T00:00:00Z")
    assert result["status"] == "ANSWER"
    assert result["telemetry"]["candidates"] == 4
    assert result["telemetry"]["excluded_expired"] == 2
    assert {hit["doc_id"] for hit in result["results"]}.isdisjoint({"synthetic-0", "synthetic-1"})


@pytest.mark.parametrize("expiry", ["missing", "invalid", None])
def test_invalid_or_missing_card_expiry_is_never_current(expiry):
    engine = engine_with_expiries([expiry] * 5)
    result = engine.ask("retry network", "2026-01-02T00:00:00Z")
    assert result["status"] == "ABSTAIN"
    assert result["results"] == []


def test_before_expiry_and_legacy_rows_remain_eligible():
    engine = engine_with_expiries(["2026-01-02T00:00:00Z"] * 4 + ["legacy"])
    result = engine.ask("retry network", "2026-01-01T23:59:59Z")
    assert result["status"] == "ANSWER"
    assert result["telemetry"]["candidates"] == 5
