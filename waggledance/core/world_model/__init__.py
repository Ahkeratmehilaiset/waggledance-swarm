# SPDX-License-Identifier: BUSL-1.1
"""World model — Phase 9 §I.

Strictly distinct from self_model (Session B). The world model
answers: what changed EXTERNALLY, what is causal in the environment,
what is uncertain externally, what belongs to the world vs the self.

May later become a fifth evidence plane (Phase Q+); for Phase 9 it
provides snapshot + delta + calibration-drift signals consumed by
the autonomy kernel.
"""

WORLD_MODEL_SCHEMA_VERSION = 1

FACT_KINDS = (
    "observation",
    "measurement",
    "report",
    "external_event",
    "external_change",
)

PREDICTION_HORIZONS = (
    "immediate",
    "short_term",
    "medium_term",
    "long_term",
)

DEFAULT_DRIFT_THRESHOLD = 0.20   # |systemic shift| triggers alert
DEFAULT_DRIFT_WINDOW = 5         # require N observations to evaluate
