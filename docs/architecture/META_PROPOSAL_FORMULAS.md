# Meta-proposal formulas

Crown-jewel area, Phase 8.5 Session D.

## Constants

- evidence planes: `("curiosity", "self_model", "dream", "resilience")`
- primary planes: `("curiosity", "self_model", "dream")`
- `CROSS_PLANE_SUPPORT_FACTOR_CAP = 1.75` (assumes max 4 planes)
- severity normalization: `low → 0.33`, `medium → 0.66`, `high → 1.0`

## Cross-plane support factor (D.txt §D5)

```
cross_plane_support_factor = min(
    1.0 + 0.25 × (num_supporting_planes − 1),
    1.75,
)
```

The cap of `1.75` assumes at most 4 evidence planes. If a future
session adds a fifth plane (e.g. campaign telemetry), this cap must
be reviewed.

| num_supporting_planes | factor |
|---|---|
| 0 | 1.00 |
| 1 | 1.00 |
| 2 | 1.25 |
| 3 | 1.50 |
| 4 | 1.75 |
| ≥5 | 1.75 (capped) |

## Urgency factor (D.txt §D5)

```
urgency_factor =
    1.20 if proposal_type == "infrastructure_followup"
              AND R7.5 marks a best_effort boundary that blocks safe scaling
    1.15 if proposal_type == "introspection_gap"
              AND Session B reports active calibration_oscillation
    1.10 if proposal_type == "topology_subdivision"
              AND under_pressure is persistent
    1.00 otherwise
```

## Confidence (D.txt §D5)

```
confidence =
      0.40 if supporting evidence exists from a primary plane
    + 0.20 if evidence exists from a second plane
    + 0.20 if dream/replay evidence includes positive structural gain
    + 0.20 if no major contradiction exists among evidence planes
```

Clamped to `[0, 1]`. Maximum value is `1.0`.

## Proposal priority (D.txt §D5)

```
proposal_priority =
    expected_value
  × confidence
  × cross_plane_support_factor
  × urgency_factor
```

`expected_value` is in `[0, 1]`; `confidence` is in `[0, 1]`;
`cross_plane_support_factor` is in `[1.0, 1.75]`; `urgency_factor`
is in `[1.0, 1.20]`.

**`proposal_priority` is intentionally UNCLAMPED.** It is a ranking
score, not a confidence score. Maximum theoretical value is
`1.0 × 1.0 × 1.75 × 1.20 = 2.10`. The schema declares it as
`{"type": "number", "minimum": 0}` with no maximum.

## Evidence strength

```
evidence_strength = avg(it.severity for it in target_items)
                    clamped to [0, 1]
```

Used to gate weak-evidence cases via `min_evidence` (default `0.10`):
items with `evidence_strength < min_evidence` and no firing
proposal_type rule are emitted under `rejected_candidates`.

## Recommended next human action

```
recommended_next_human_action =
    "post_campaign_runtime_review_candidate"
        if confidence ≥ 0.60
           and priority ≥ 0.05
           and scope_class ∈ {topology, solver_library, policy}
    "review_for_future_PR"
        if confidence ≥ 0.40 and priority ≥ 0.02
    "archive_as_low_value"
        if confidence < 0.30 and priority < 0.01
    "wait_for_more_evidence"
        otherwise
```

## Trigger rules (proposal_type inference)

| Output type | Predicate |
|---|---|
| `topology_subdivision` | dream_subdivision_hint AND `len(planes ∩ primary) ≥ 2` |
| `solver_family_growth` | `"dream" ∈ planes` AND `("curiosity" ∈ planes OR "self_model" ∈ planes)` |
| `policy_gate_adjustment` | `calibration_oscillation_active` |
| `introspection_gap` | `"self_model" ∈ planes` AND `severity_max ≥ 0.66` |
| `infrastructure_followup` | `"resilience" ∈ planes` AND `r7_5_blocks` |
| (no rule) | downgrade to `insufficient_evidence` or `rejected_candidates` |

Single-plane evidence is allowed only for `introspection_gap` and
`infrastructure_followup`. All other types require ≥2 planes (the
deterministic join key shares a `canonical_target`).

## Multi-plane requirement (D.txt §D4)

A proposal whose type requires multi-plane support but has only one
supporting plane is **never emitted as a full proposal**. It is
downgraded to `review_bundle.insufficient_evidence[]` with:

```
{
  "candidate_target": "<target>",
  "missing_planes": [<sorted missing planes>],
  "evidence_strength_seen": <float>,
  "why_below_threshold": "single-plane evidence is only allowed for introspection_gap or infrastructure_followup"
}
```

This rule keeps `hive_proposals.json` focused on actionable items.

## Deviation policy

The agent may deviate from these formulas only by documenting the
alternative explicitly in this file. No silent substitution.
