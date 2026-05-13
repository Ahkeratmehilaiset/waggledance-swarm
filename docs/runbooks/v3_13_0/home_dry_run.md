# HOME Dry-Run Runbook: `electricity_spot_optimizer` (v3.13.0)

**Profile:** HOME
**Anchor solver:** `electricity_spot_optimizer`
**Status:** dry-run runbook for v3.13.0; uses synthetic data only.
**Sprint 2 dependencies:** `DocIngest` parser (Step 2) and the
  end-to-end CLI wrapper are not in v3.13.0. For v3.13.0, Steps 2
  and 4 require the operator to invoke parsers and the runtime
  manually. The runbook is published now so the v3.13.0 substrate
  (WriteRCOGate, SolverProvenance, ShadowRunner, DivergenceAnalyzer)
  has a documented end-to-end target.

## Purpose

End-to-end dry-run flow for the `electricity_spot_optimizer` solver
template as a HOME profile run. Parameterised, country-agnostic, uses
**no operator-specific data**. The runbook is the bridge between
v3.13.0 schemas (`SCH-001` ToolDescriptor, `SCH-002` StateHandle,
`SCH-005` SolverCandidateManifest, `SCH-009` ProfileConfig) and the
operator-visible flow.

Reads hourly spot price feed + user consumption forecast and
**recommends** optimal charge/discharge schedules for battery, EV, or
HVAC windows. Recommendation only; v3.13.0 emits no external action
via this runbook.

## Prerequisites

| Prerequisite | Source | Notes |
|---|---|---|
| `ProfileConfig` for the operator | `SCH-009` | `profile_kind=home`, country (ISO 3166-1 alpha-2), language, timezone, currency |
| Spot price feed access | `ADAPT-015` utility_provider_portal | Public for some countries (Nord Pool, ENTSO-E, EIA), authenticated for others |
| Consumption history (synthetic OK for dry-run) | `StateHandle` (sqlite, internal) | Last 30 days hourly OK for v3.13.0 dry-run |
| Tariff structure document | `ADAPT-001` document_corpus | Operator's utility tariff PDF / regulator publication |

## Step-by-step

### Step 1: ProfileConfig

```yaml
schema_version: 1
profile_id: home_demo
profile_kind: home
country: FI                            # example
timezone: Europe/Helsinki
language: fi
currency: EUR
service_provider_refs:
  - provider:helen_spot_fi
data_residency_policy: policy:eu_only
external_write_policy_ref: policy:home_no_external_writes
redaction_policy_ref: policy:home_default_redaction
default_risk_policy: external_effect_requires_rco
operator_review_required: true
credential_vault_impl: os_keyring
retrieval_overrides:
  context_sim_threshold: 0.58
  context_top_n: 8
```

### Step 2: DocIngest (manual for v3.13.0; automated in Sprint 2+)

Operator drops inputs into a local directory (e.g.
`iterations/anchor_use_case/dry_run/home/inputs/`):
- Tariff structure PDF (or sanitised excerpt)
- Hourly consumption sample (synthetic CSV, 30 days)
- ProfileConfig YAML

DocIngest (Sprint 2+) parses these and emits a
`solver_candidate_proposal` MAGMA event. For v3.13.0 dry-run, the
operator can manually invoke the parser.

### Step 3: SolverCandidateManifest assembly

The candidate manifest (`SCH-005`) is filled with:

```python
SolverCandidateManifest(
    candidate_id="electricity_spot_optimizer_home_demo_001",
    source_docs=["doc:tariff_structure_pdf", "doc:consumption_sample_csv"],
    source_tools=[],                   # no operator's existing
                                        # script in dry-run
    training_contracts=["ctr_date", "ctr_search", "ctr_vector",
                         "ctr_memory", "ctr_cross_ref"],
    state_handles=["state:spot_price_store",
                    "state:consumption_forecast",
                    "state:optimizer_recommendations"],
    connector_handles=["conn:spot_price_public_feed"],
    shadow_inputs=["synth_24h_winter", "synth_24h_summer"],
    shadow_expected_outputs=[
        "recommendation_with_savings_estimate_winter",
        "recommendation_with_savings_estimate_summer",
    ],
    divergence_score=None,             # filled by shadow run
    accepted_differences=[],
    rejected_differences=[],
    promotion_decision="awaiting_shadow",
    rollback_plan="recovery:spot_optimizer_v1",
    operator_review_id="op_review_001",
    provenance_signatures=[],          # filled during RCO
    activation_state="unactivated",
)
```

This example manifest is validated against
`schemas/v3_13_0/solver_candidate_manifest.schema.json` in
`tests/contracts/test_runbook_examples_v3_13_0.py` so the runbook
cannot silently drift from the schema.

### Step 4: Shadow run

`ShadowRunner` invokes the candidate against the synthetic input sets.
Output is recommendation text + structured savings estimate.
Baseline: a trivial heuristic ("charge during cheapest 3 hours")
provides ground truth.

**No CLI wrapper ships in v3.13.0.** The runtime is wired by an
operator-supplied harness (Sprint 2 `DocIngest` + CLI). For v3.13.0
the equivalent is a direct Python call against the
`ShadowRunner` API:

```python
# Pseudocode -- ShadowRunner has no __main__ in v3.13.0.
# Operator wires the hooks and calls run() directly.
from waggledance.core.v3_13_0.shadow_runner import (
    ShadowRunner, ShadowRunInput,
)

runner = ShadowRunner(
    fetch_tool_descriptor=...,           # operator-supplied
    fetch_profile_config=...,
    run_candidate=...,
    run_baseline=...,                    # exit_code must be 0; non-zero
                                         # aborts with shadow.baseline_failed
    compare_outputs=...,                 # DivergenceAnalyzer.compare hook
    emit_magma_event=...,
    state_handle_is_operator_owned=...,
)
result = runner.run(ShadowRunInput(
    candidate_manifest_id="electricity_spot_optimizer_home_demo_001",
    shadow_input_set_ref="capture:synth_24h_winter",
    profile_config_ref="profile:home_demo",
    tool_descriptor_id="tool_electricity_spot_optimizer",
    state_handles=["state:dry_run_home_winter"],
    operator_baseline_command=["python", "tools/spot_baseline.py",
                                "--input", "synth_24h_winter"],
    expected_output_format="json",
))
```

Expected `ShadowRunResult` on this synthetic input:
- `divergence_score` numeric, low (typically `0.0` for identical
  output or close to `0.0` for noise-only diff). The
  `DivergenceCategory` (a separate field on the
  `DivergenceArtifact`) classifies the score: `identical`,
  `near_match`, `partial_match`, `divergent`, `incomparable`.
- No `WRT-003 external_effect_blocked` events (this solver is
  informational-only).
- `shadow.run_completed` MAGMA event emitted (vs
  `shadow.baseline_failed` if the operator's baseline command
  exits non-zero).

### Step 5: DivergenceAnalyzer

Compares candidate output to baseline. For
`electricity_spot_optimizer` template family, severity rules:
- `numeric_value_within_5pct` -> `noise`
- `ranking_changed_within_top3` -> `material`
- `missing_window_recommendation` -> `critical`

Expected: divergence score `< 0.05` for the dry-run inputs.

### Step 6: Solver-RCO + provenance signing

Owner agent (Claude) emits:

```python
{
  "type": "handoff",
  "status": "rco_requested",
  "payload": {"kind": "solver",
              "solver_candidate_id": "electricity_spot_optimizer_home_demo_001",
              "signing_role": "owner",
              "solver_manifest_hash": "<sha256>"},
}
```

Peer (Codex) reviews and emits:

```python
{
  "type": "handoff",
  "status": "rco_pass",
  "payload": {"kind": "solver",
              "solver_candidate_id": "electricity_spot_optimizer_home_demo_001",
              "signing_role": "peer",
              "solver_manifest_hash": "<sha256>"},
}
```

Both signatures recorded in MAGMA via `SolverProvenance.sign()`.
Manifest gains `activation_state = signed`.

Per spec edit E16 the bridge events use the existing
`type=handoff` value with `payload.kind=solver` as the discriminator;
no invented dotted bridge types.

### Step 7: Operator review (dry-run only)

Operator reads the solver manifest + shadow output + divergence
artifact. For dry-run, operator decides:
- Accept and graduate to hybrid (next sprint, with real data)
- Reject with feedback
- Iterate the candidate

**No actual external write occurs in v3.13.0 dry-run.** The runbook
ends here. Promotion to hybrid (and writing to real downstream
systems) requires Sprint 2+ components and a real operator
ProfileConfig under a policy that explicitly allows external_effect
writes.

## Stop conditions

If any of these fire, the runbook pauses and escalates:
- `ANTI-001`: bulk read of spot price feed without window
- `ANTI-004`: credential found in ProfileConfig or tariff doc
- `ANTI-006`: spot feed rate limit exceeded
- `WRT-003` attempt: any actual write to user's home automation
- Operator scope policy denies the run

## Acceptance criteria

This runbook is "passing" if a fresh v3.13.0 install can:
1. Load the ProfileConfig
2. Parse the synthetic inputs (Sprint 2 DocIngest, or manual for v3.13.0)
3. Construct a `SolverCandidateManifest`
4. Run shadow with deterministic output
5. Score divergence
6. Complete the solver-RCO handshake on the bridge
7. Display result to operator in a coherent flow

No personal data is required at any step. Synthetic inputs only.
