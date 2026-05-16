from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas" / "v3_13_0" / "idle_protocol.v1.json"


def _validator() -> jsonschema.Draft7Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def good_idle_proposal() -> dict:
    return {
        "protocol_version": "idle-protocol.v1",
        "event_type": "idle_proposal",
        "proposal_id": "idle-prop-20260516-001",
        "round_number": 1,
        "proposes_substrate_change": True,
        "problem_statement": "Strategic bridge events can wait indefinitely when no PR vehicle exists.",
        "proposal": "Introduce an opt-in idle protocol that starts only after all idle predicates hold.",
        "tradeoff_axis": "More autonomous strategic deliberation versus bounded operator-gated execution.",
        "simulation_evidence": {
            "kind": "scenario_simulation",
            "summary": "A four hour strategic silence would have produced an idle proposal after the first idle hour.",
            "artifacts": [
                {
                    "path": ".agent-bridge/shared/events.jsonl",
                    "description": "Bridge timeline used to identify the idle window.",
                }
            ],
        },
        "charter_alignment": {
            "compatible": True,
            "reasoning": "The protocol only emits operator-gated consensus events and does not auto-execute work.",
        },
    }


def good_idle_counter_proposal() -> dict:
    event = copy.deepcopy(good_idle_proposal())
    event.update(
        {
            "event_type": "idle_counter_proposal",
            "proposal_id": "idle-prop-20260516-002",
            "round_number": 2,
            "responds_to": "idle-prop-20260516-001",
            "alternative_proposal": "Keep idle detection external and emit only deliberation prompts in v1.",
            "reasoning_points": [
                "External detection keeps production agents unchanged during v1 validation.",
                "Manual invocation makes false positives directly observable by the operator.",
                "A schema-first rollout lets validators reject low-quality deliberation before automation exists.",
            ],
        }
    )
    del event["proposal"]
    return event


def good_idle_adversarial_review() -> dict:
    event = copy.deepcopy(good_idle_proposal())
    event.update(
        {
            "event_type": "idle_adversarial_review",
            "proposal_id": "idle-prop-20260516-003",
            "round_number": 3,
            "responds_to": "idle-prop-20260516-002",
            "counterexamples": [
                "A long-running CI outage could look idle unless the detector treats pending checks as active.",
                "Two agents may converge on a schema change that is correct syntactically but operationally noisy.",
            ],
        }
    )
    del event["proposal"]
    return event


def good_idle_consensus() -> dict:
    event = copy.deepcopy(good_idle_proposal())
    event.update(
        {
            "event_type": "idle_consensus_reached",
            "proposal_id": "idle-prop-20260516-005",
            "round_number": 5,
            "proposes_substrate_change": False,
            "consensus_target_proposal_id": "idle-prop-20260516-002",
            "operator_gate_required": True,
            "auto_execute": False,
        }
    )
    del event["proposal"]
    return event


def good_idle_charter_violation() -> dict:
    event = copy.deepcopy(good_idle_proposal())
    event.update(
        {
            "event_type": "idle_charter_violation",
            "proposal_id": "idle-prop-20260516-004",
            "round_number": 4,
            "proposes_substrate_change": False,
            "violating_proposal_id": "idle-prop-20260516-003",
            "violation_reason": "The proposal would convert consensus directly into executable scout tasks.",
            "terminate_protocol": True,
            "operator_escalation_required": True,
            "charter_alignment": {
                "compatible": False,
                "reasoning": "Auto-execution would bypass the required operator gate for consensus decisions.",
            },
        }
    )
    del event["proposal"]
    return event


def test_idle_protocol_schema_is_valid_draft7() -> None:
    _validator()


def test_valid_idle_events_validate() -> None:
    validator = _validator()
    for event in [
        good_idle_proposal(),
        good_idle_counter_proposal(),
        good_idle_adversarial_review(),
        good_idle_consensus(),
        good_idle_charter_violation(),
    ]:
        validator.validate(event)


def test_idle_event_requires_simulation_evidence() -> None:
    event = good_idle_proposal()
    del event["simulation_evidence"]

    assert list(_validator().iter_errors(event))


def test_counter_proposal_requires_three_reasoning_points() -> None:
    event = good_idle_counter_proposal()
    event["reasoning_points"] = event["reasoning_points"][:2]

    assert list(_validator().iter_errors(event))


def test_adversarial_review_is_fixed_to_round_three_in_v1() -> None:
    event = good_idle_adversarial_review()
    event["round_number"] = 4

    assert list(_validator().iter_errors(event))


def test_consensus_cannot_auto_execute_or_skip_operator_gate() -> None:
    validator = _validator()
    auto_execute = good_idle_consensus()
    auto_execute["auto_execute"] = True
    no_gate = good_idle_consensus()
    no_gate["operator_gate_required"] = False

    assert list(validator.iter_errors(auto_execute))
    assert list(validator.iter_errors(no_gate))


def test_charter_violation_requires_termination_and_escalation() -> None:
    validator = _validator()
    no_termination = good_idle_charter_violation()
    no_termination["terminate_protocol"] = False
    no_escalation = good_idle_charter_violation()
    no_escalation["operator_escalation_required"] = False

    assert list(validator.iter_errors(no_termination))
    assert list(validator.iter_errors(no_escalation))
