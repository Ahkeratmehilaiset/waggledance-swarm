# SPDX-License-Identifier: BUSL-1.1
"""Dependency-free contracts for logical hex-cell query assignment."""

import pytest

from waggledance.core.hex_cell_topology import CELL_SAFETY, HexCellTopology


_INTENT_BASELINES = {
    "math": "math",
    "thermal": "thermal",
    "optimization": "energy",
    "seasonal": "seasonal",
    "constraint": "safety",
    "stats": "system",
    "statistical": "system",
    "symbolic": "math",
    "causal": "general",
    "anomaly": "system",
    "retrieval": "general",
    "chat": "general",
}

_TYPED_INTENT_BASELINES = {
    intent: cell
    for intent, cell in _INTENT_BASELINES.items()
    if cell != "general"
}


@pytest.mark.parametrize("intent", _INTENT_BASELINES)
@pytest.mark.parametrize(
    "query",
    (
        "Palovaroitin piippaa; lämpötila heat celsius.",
        "Fire alarm; temperature heat celsius.",
        "Fire safety alarm; temperature heat celsius.",
        "Smoke detected; calculate the average percent.",
        "Smoke was detected; calculate the average percent.",
        "Smoke has been detected; calculate the average percent.",
    ),
)
def test_safety_text_preempts_ordinary_domain_intent(intent, query):
    assignment = HexCellTopology().assign_cell(intent, query)

    assert assignment.cell_id == CELL_SAFETY
    assert assignment.method == "keyword"


@pytest.mark.parametrize("intent, expected_cell", _TYPED_INTENT_BASELINES.items())
@pytest.mark.parametrize(
    "query",
    (
        "Run a smoke test for the software build.",
        "Set an alarm clock for model training.",
        "Calculate the relative risk percentage.",
        "Calculate the fire sale discount percent.",
        "Calculate the fire sale discount and set an alarm clock.",
        "The smoke test passed; an anomaly was detected in software.",
    ),
)
def test_ambiguous_safety_words_do_not_preempt_typed_intent(
    intent,
    expected_cell,
    query,
):
    assignment = HexCellTopology().assign_cell(intent, query)

    assert assignment.cell_id == expected_cell
    assert assignment.method == "intent"


@pytest.mark.parametrize("intent, expected_cell", _INTENT_BASELINES.items())
def test_generic_beeping_does_not_trigger_safety_override(intent, expected_cell):
    assignment = HexCellTopology().assign_cell(
        intent,
        "A generic device is beeping.",
    )

    assert assignment.cell_id == expected_cell
    assert assignment.method == "intent"
