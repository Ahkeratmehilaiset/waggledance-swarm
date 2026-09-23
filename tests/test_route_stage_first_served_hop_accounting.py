from __future__ import annotations

import pytest

from waggledance.adapters.http.routes.chat import RouteStageRuntimeMetrics


def _first_served_hop_counts(*, authority=...) -> dict[str, float]:
    hybrid_event = {
        "stage": "hybrid_retrieval_8_cell",
        "answered": True,
        "retrieval_mode": "hybrid:candidate",
    }
    if authority is not ...:
        hybrid_event["authoritative"] = authority
    metrics = RouteStageRuntimeMetrics()
    metrics.record(
        [
            hybrid_event,
            {"stage": "orchestrator_llm_fallback", "source": "llm"},
        ],
        1.0,
    )
    return metrics.snapshot()["first_served_hop_total"]


@pytest.mark.parametrize("authority", [..., False, "true", 1])
def test_non_authoritative_hybrid_answer_falls_through_to_llm(authority) -> None:
    counts = _first_served_hop_counts(authority=authority)

    assert counts["hybrid_retrieval_8_cell"] == 0.0
    assert counts["orchestrator_llm_fallback"] == 1.0


def test_literal_authoritative_hybrid_answer_is_first_served_hop() -> None:
    counts = _first_served_hop_counts(authority=True)

    assert counts["hybrid_retrieval_8_cell"] == 1.0
    assert counts["orchestrator_llm_fallback"] == 0.0
