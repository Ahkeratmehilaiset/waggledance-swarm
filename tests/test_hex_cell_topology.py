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
        "Fire alarm is going off; temperature heat celsius.",
        "Fire safety alarm is sounding; temperature heat celsius.",
        "Fire in the kitchen; the alarm is going off.",
        "There is a fire and the alarm has gone off.",
        "A fire started upstairs and the hallway alarm is sounding.",
        "The fire alarm system is going off right now.",
        "The alarm system is sounding because there is a fire.",
        "The fire alarm is going off; this is not a drill.",
        "Not only was smoke detected, but flames were visible.",
        "Not sure why palovaroitin piippaa; calculate the average.",
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
        "What's the fire alarm test schedule this month?",
        "Please get a quote for a new fire alarm system.",
        "Calculate the fire alarm installation cost.",
        "Run a simulated fire alarm drill for model training.",
        "No smoke was detected during the software test.",
        "No visible smoke was detected during the software test.",
        "No toxic smoke was detected during the software test.",
        "I'm not sure smoke was detected.",
        "Not sure whether smoke was detected.",
        "The fire alarm is not going off; run diagnostics.",
        "Palovaroitin ei piippaa; run diagnostics.",
        "Palovaroitin ei enää piippaa; run diagnostics.",
        "Palovaroitin ei soi; run diagnostics.",
        "Explain how a fire alarm system works.",
        "Find a palovaroitin manual for model training.",
        "The fire alarm is sounding as part of a scheduled drill.",
        "The fire alarm is going off; this is only a test.",
        "A fire sale starts while the alarm clock is going off.",
        "A fire sale started. Later, the alarm is going off.",
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
