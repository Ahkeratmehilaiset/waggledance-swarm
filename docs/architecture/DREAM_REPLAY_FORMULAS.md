# Dream replay formulas

Crown-jewel area, Phase 8.5 Session C.

## Constants

- `MAX_REPLAY_CASES = 50`
- `MIN_GAIN_RATIO = 0.10`
- `HISTORY_WINDOW = 10`
- severity normalization: `low → 0.33`, `medium → 0.66`, `high → 1.0`

## Curriculum priority (c.txt §C1)

```
dream_priority =
    tension_severity
  × calibration_gap
  × recurrence_factor
  × expected_value
  × (1 + blind_spot_bonus + exploration_bonus)
```

- `tension_severity ∈ [0,1]` (string-or-number normalized)
- `calibration_gap = |prior_score − evidence_implied_score|` for the
  most recent calibration correction on the dimension; `0.0` when
  none. Floored at `0.05` inside the planner so absent calibration
  data doesn't zero the product.
- `recurrence_factor = 1 + min(1.0, persistence_count / 3)`
- `blind_spot_bonus = 0.25` (high) / `0.10` (medium) / `0.0` else
- `exploration_bonus = 0` on bootstrap (no prior dream history)

## Replay metrics (c.txt §C6)

For each replay case the structural-gain proxy returns:

- `structural_gain = true` iff the case has a
  `suspected_missing_capability` or `bridge_candidate_refs` that the
  shadow graph diff realizes and the live graph does not
- `regression = true` iff a protected case's
  `original_resolution_hash` is no longer reachable in shadow

Aggregated:

```
replay_case_count               = |selected cases|
targeted_case_count             = |cases with capability/bridge tag|
structural_gain_count           = Σ structural_gain
protected_case_regression_count = Σ regression where protect=true
unresolved_case_count           = |cases with neither gain nor regression|
estimated_fallback_delta        = structural_gain_count / replay_case_count
                                  (or undefined when replay_case_count == 0)
```

`replay_methodology = "structural_proxy_v0.1"`.

## Structurally promising (c.txt §C6 default conservative rule)

```
structurally_promising =
    collapse_passed
  AND structural_gain_count > 0
  AND protected_case_regression_count == 0
  AND (structural_gain_count / max(targeted_case_count, 1)) >= MIN_GAIN_RATIO
```

## Confidence (c.txt §C7 default)

```
confidence =
    (gate_passed ? 0.5 : 0.0)
  + (no_protected_regressions ? 0.3 : 0.0)
  + (structural_gain_ratio >= MIN_GAIN_RATIO ? 0.2 : 0.0)
```

Clamped to `[0, 1]`.

The agent may deviate from this formula only by documenting the
alternative here in writing. No silent substitution.

## Expected value of merging (c.txt §C7)

```
expected_value_of_merging =
    estimated_fallback_delta
  × tension_severity_max
  × confidence
```

`tension_severity_max` = max normalized severity among targeted
tensions. Result is clamped to `[0, 1]` by construction since each
factor is in `[0, 1]`.

## Methodology limitations

- This is a STRUCTURAL counterfactual proxy only. No live solvers
  are executed in shadow mode.
- Results indicate structural opportunity, not validated correctness.
- Human review is required before any merge.
- True execution-based counterfactual replay is deferred to a later
  session with explicit permission for offline inference.
