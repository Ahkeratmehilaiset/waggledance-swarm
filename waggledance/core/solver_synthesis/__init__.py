# SPDX-License-Identifier: BUSL-1.1
"""Solver synthesis — Phase 9 §U1 / §U2 / §U3.

Phase U1 ships declarative solver families + bootstrap. Free-form
code generation is reserved for U3 (gap_to_solver_spec residual
path). U1 always wins when match_confidence > 0.8 because it is
faster, cheaper, more auditable, and less hallucination-prone.
"""

from waggledance.core.hex_cell_topology import ALL_CELLS

SOLVER_SYNTHESIS_SCHEMA_VERSION = 1

# 10 initial solver families per Prompt_1_Master §U1
SOLVER_FAMILY_KINDS = (
    "scalar_unit_conversion",
    "lookup_table",
    "threshold_rule",
    "interval_bucket_classifier",
    "linear_arithmetic",
    "weighted_aggregation",
    "temporal_window_rule",
    "bounded_interpolation",
    "structured_field_extractor",
    "deterministic_composition_wrapper",
)

# Immutable local view of the canonical top-level topology.
HEX_CELLS = tuple(ALL_CELLS)

# U1→U3 escalation thresholds per Prompt_1_Master §U1 ROUTING RULES
U1_HIGH_CONFIDENCE_THRESHOLD = 0.8
U1_LOW_CONFIDENCE_THRESHOLD = 0.5
