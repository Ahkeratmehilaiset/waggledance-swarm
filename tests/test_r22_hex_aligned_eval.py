"""R22.2 — hex-aligned eval pin tests.

Per operator routing 2026-05-10 (response to R22 final routing):

R21.1 used `tests/oracle/*.yaml` whose `cell:` field used the
energy/math/safety/system/thermal taxonomy (decision-type), which did
not match `configs/hex_cells.yaml`'s hub/bee_ops/environment/
home_comfort/safety_security/production/logistics taxonomy (domain).
The result was a control_quality=0.5 that did not reflect the
underlying heuristic's competence.

R22.2 ships `tests/oracle_hex/*.yaml` with utterances explicitly aligned
to the seven hex cells. With this corpus, the heuristic
`HexTopologyRegistry.select_origin_cell` reaches control_quality=0.7619
— a +52.4% absolute lift in the Axis B baseline. The remaining gap
(0.2381) is paraphrased utterances that intentionally avoid the
selector keywords; that gap is the headroom an LLM treatment can
target in R22.3.

These tests pin:

1. corpus shape: 7 files, 15 positive + 5 negative each = 140 utterances
2. per-cell heuristic score (catches selector regressions)
3. macro quality regression floor (Axis B)
4. negative routing: 100% across all cells (cross-cell discrimination)
"""
from __future__ import annotations

from pathlib import Path

import yaml

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.run_r21_oracle_ab_proof import (  # noqa: E402
    load_oracle_corpus,
    quality_arm,
)
from waggledance.application.services.hex_topology_registry import (  # noqa: E402
    HexTopologyRegistry,
)


HEX_ORACLE_DIR = REPO_ROOT / "tests" / "oracle_hex"
HEX_CONFIG = REPO_ROOT / "configs" / "hex_cells.yaml"

EXPECTED_CELLS = {
    "hub",
    "bee_ops",
    "environment",
    "home_comfort",
    "safety_security",
    "production",
    "logistics",
}


def _heuristic_route(registry: HexTopologyRegistry, utterance: str):
    """Mirror the harness's heuristic-arm route function."""
    return registry.select_origin_cell(utterance)


def _load_registry():
    return HexTopologyRegistry(config_path=str(HEX_CONFIG), agents=[])


def test_r22_corpus_shape_140_utterances_across_7_cells():
    """7 hex cells × (15 positive + 5 negative) = 140 utterances total."""
    oracles = load_oracle_corpus(HEX_ORACLE_DIR)
    assert len(oracles) == 7, f"expected 7 hex-aligned oracle files, got {len(oracles)}"
    cells = {o["cell"] for o in oracles}
    assert cells == EXPECTED_CELLS, f"oracle cells must match hex registry; got {cells}"
    for o in oracles:
        assert len(o["positive"]) == 15, (
            f"{o['file']}: expected 15 positive utterances, got {len(o['positive'])}"
        )
        assert len(o["negative"]) == 5, (
            f"{o['file']}: expected 5 negative utterances, got {len(o['negative'])}"
        )
    total_pos = sum(len(o["positive"]) for o in oracles)
    total_neg = sum(len(o["negative"]) for o in oracles)
    assert total_pos == 105
    assert total_neg == 35
    assert total_pos + total_neg == 140


def test_r22_heuristic_negative_routing_perfect():
    """Cross-cell discrimination: every negative utterance must NOT route
    to the cell that owns it. This is what makes the negative score 5/5
    per cell across the board on heuristic — substring matching is
    conservative enough that a frost-forecast utterance does not land
    in bee_ops just because it shares some letters."""
    registry = _load_registry()
    oracles = load_oracle_corpus(HEX_ORACLE_DIR)

    def route_fn(u):
        return _heuristic_route(registry, u)

    result = quality_arm(oracles, route_fn)
    for f in result["per_file"]:
        assert f["neg_correct"] == f["neg_total"], (
            f"{f['file']}: negative routing must be 100%; "
            f"got {f['neg_correct']}/{f['neg_total']}"
        )


def test_r22_heuristic_macro_quality_floor():
    """Axis B regression floor for the heuristic baseline.

    R21.1 measured control_quality = 0.5 on the topology-mismatched
    oracle. R22.2 with hex-aligned utterances measures 0.7619. The
    remaining 0.2381 is paraphrase headroom for LLM treatment in R22.3.

    The floor pinned here is 0.75 — slightly below 0.7619 to absorb
    minor selector-config tweaks but well above the 0.5 mismatched
    baseline. If a future change drops the heuristic below this floor,
    that is a regression to investigate (likely a hex-cell selector
    edit that broke positive coverage).
    """
    registry = _load_registry()
    oracles = load_oracle_corpus(HEX_ORACLE_DIR)

    def route_fn(u):
        return _heuristic_route(registry, u)

    result = quality_arm(oracles, route_fn)
    quality = result["quality"]
    assert quality >= 0.75, (
        f"heuristic macro quality regression: got {quality:.4f}, "
        f"floor is 0.75 (R22.2 pinned at 0.7619)"
    )
    assert quality > 0.5 + 0.20, (
        "R22.2 must clear R21.1 control_quality=0.5 by >= 20 pts; "
        f"got {quality:.4f}"
    )


def test_r22_per_cell_quality_no_cell_below_floor():
    """Each individual cell must score at least 0.6 — guards against a
    single cell collapsing (e.g. a removed selector) while the macro
    average masks it."""
    registry = _load_registry()
    oracles = load_oracle_corpus(HEX_ORACLE_DIR)

    def route_fn(u):
        return _heuristic_route(registry, u)

    result = quality_arm(oracles, route_fn)
    for f in result["per_file"]:
        assert f["file_score"] >= 0.6, (
            f"cell {f['cell']} below per-cell floor: "
            f"got {f['file_score']:.4f}, floor 0.6"
        )
