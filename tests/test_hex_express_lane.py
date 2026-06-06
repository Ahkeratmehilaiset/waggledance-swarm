import json
from pathlib import Path

import jsonschema
import pytest

from waggledance.core.hex_cell_topology import ALL_CELLS
from waggledance.core.hex_topology.express_lane import (
    HEX_EXPRESS_LANE_PLAN_SCHEMA_VERSION,
    ExpressLaneEdge,
    ExpressLaneRequest,
    HexExpressLaneError,
    plan_express_lane,
)


ROOT = Path(__file__).resolve().parent.parent


def _request(**overrides):
    data = {
        "source_cell_id": "learning",
        "intent_tags": ["cell_imaging", "segmentation"],
        "required_capability_tags": ["segmentation"],
        "trust_min": 0.85,
        "freshness_max_age_days": 30,
        "cost_cap": "medium",
    }
    data.update(overrides)
    return ExpressLaneRequest(**data)


def _edge(**overrides):
    data = {
        "edge_id": "edge.learning.to.system.segmentation",
        "source_cell_id": "learning",
        "target_cell_id": "system",
        "capability_tags": ["cell_imaging", "segmentation", "statistics"],
        "trust_score": 0.92,
        "freshness_age_days": 7,
        "cost_class": "medium",
        "rationale": "Use specialist segmentation/statistics lane.",
    }
    data.update(overrides)
    return ExpressLaneEdge(**data)


def test_selects_distant_specialist_when_constraints_pass() -> None:
    plan = plan_express_lane(
        _request(),
        [_edge()],
        known_cell_ids=ALL_CELLS,
    )

    assert plan["schema_version"] == HEX_EXPRESS_LANE_PLAN_SCHEMA_VERSION
    assert plan["selected"] is True
    assert plan["route"]["target_cell_id"] == "system"
    assert plan["route"]["matched_capability_tags"] == [
        "cell_imaging",
        "segmentation",
    ]
    assert plan["no_runtime_mutation"] is True
    assert plan["gate_skip_authority"] is False
    assert plan["solver_call_authority"] is False
    assert plan["clinical_decision_authority"] is False
    assert plan["receipt_required"] is True


def test_rejects_unsafe_or_stale_edges_without_selecting() -> None:
    plan = plan_express_lane(
        _request(),
        [
            _edge(edge_id="edge.lowtrust", trust_score=0.6),
            _edge(edge_id="edge.stale", freshness_age_days=90),
            _edge(edge_id="edge.expensive", cost_class="high"),
            _edge(edge_id="edge.missing", capability_tags=["cell_imaging"]),
        ],
        known_cell_ids=ALL_CELLS,
    )

    assert plan["selected"] is False
    reasons = {item["edge_id"]: item["reason"] for item in plan["rejected_edges"]}
    assert reasons == {
        "edge.lowtrust": "trust_below_minimum",
        "edge.stale": "freshness_too_old",
        "edge.expensive": "cost_above_cap",
        "edge.missing": "missing_required_capability",
    }


def test_authority_flags_fail_closed() -> None:
    with pytest.raises(HexExpressLaneError, match="gate_skip_authority"):
        plan_express_lane(_request(), [_edge(gate_skip_authority=True)])

    with pytest.raises(HexExpressLaneError, match="clinical_decision_authority"):
        plan_express_lane(_request(), [_edge(clinical_decision_authority=True)])

    with pytest.raises(HexExpressLaneError, match="receipt_required"):
        plan_express_lane(_request(), [_edge(receipt_required=False)])


def test_unknown_cells_and_duplicate_edges_fail_closed() -> None:
    with pytest.raises(HexExpressLaneError, match="unknown target_cell_id"):
        plan_express_lane(_request(), [_edge(target_cell_id="ghost")], known_cell_ids=ALL_CELLS)

    with pytest.raises(HexExpressLaneError, match="duplicate edge_id"):
        plan_express_lane(_request(), [_edge(), _edge()], known_cell_ids=ALL_CELLS)


def test_selection_is_deterministic_by_trust_freshness_cost_target() -> None:
    plan = plan_express_lane(
        _request(required_capability_tags=["segmentation"]),
        [
            _edge(edge_id="edge.b", target_cell_id="system", trust_score=0.91),
            _edge(
                edge_id="edge.a",
                target_cell_id="math",
                trust_score=0.94,
                freshness_age_days=20,
            ),
            _edge(
                edge_id="edge.c",
                target_cell_id="general",
                trust_score=0.94,
                freshness_age_days=5,
                cost_class="low",
            ),
        ],
        known_cell_ids=ALL_CELLS,
    )

    assert plan["route"]["edge_id"] == "edge.c"


def test_schema_validates_plan_output() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "hex_express_lane.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.Draft7Validator.check_schema(schema)
    plan = plan_express_lane(_request(), [_edge()], known_cell_ids=ALL_CELLS)

    jsonschema.Draft7Validator(schema).validate(plan)
