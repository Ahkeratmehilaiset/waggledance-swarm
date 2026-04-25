"""Dreaming pipeline — Phase 8.5 Session C.

Strict layered system per c.txt §DREAM PIPELINE FOCUS:

1. Stochastic generation phase — external only, offline only.
2. Deterministic collapse phase — proposal files flow through Phase 8
   proposal-gate logic; outputs reproducible and auditable.
3. Shadow graph and structural counterfactual replay — in-memory only,
   shadow-only, no runtime mutation.
4. Meta-proposal artifact — machine-readable, auditable, NOT a runtime
   change.

Crown-jewel area per c.txt §BUSL: any non-trivial logic edit here
requires the LICENSE-BUSL.txt Change Date update to 2030-03-19 in
the same commit.
"""

DREAMING_SCHEMA_VERSION = 1
HISTORY_WINDOW = 10
MAX_REPLAY_CASES = 50
MIN_GAIN_RATIO = 0.10

# Tension severity normalization (string → [0,1])
SEVERITY_TO_FLOAT = {"low": 0.33, "medium": 0.66, "high": 1.0}

# Dream curriculum modes per c.txt §C1
DREAM_MODES = (
    "base_solver_growth",
    "solver_refinement",
    "bridge_composition",
    "subdivision_pressure",
    "introspection",
    "wait",
)

# Verdict handling per c.txt §C4
COLLAPSE_VERDICTS = (
    "ACCEPT_CANDIDATE",   # → demote to shadow-only
    "REJECT_HARD",        # → log + skip (schema/cell errors)
    "REJECT_SOFT",        # → log + skip (gate failure, ABSTAIN, inconclusive)
)
