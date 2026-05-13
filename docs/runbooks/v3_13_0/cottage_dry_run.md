# COTTAGE Dry-Run Runbook: `frost_risk_predictor` (v3.13.0)

**Profile:** COTTAGE
**Anchor solver:** `frost_risk_predictor`
**Status:** dry-run runbook for v3.13.0; uses synthetic data only.
**Sprint 2 dependencies:** Same as HOME runbook -- `DocIngest` and the
  end-to-end CLI wrapper land in Sprint 2+. For v3.13.0, the manual
  parser invocation in Step 2 and the runtime invocations in Step 4
  are operator-driven.

## Purpose

End-to-end dry-run flow for the `frost_risk_predictor` solver template
as a COTTAGE profile run. Parameterised, climate-zone-aware, uses
**no operator-specific cottage data**.

Reads weather forecast + indoor / outdoor sensor history + building
thermal model parameters and **predicts** frost risk for a remote
dwelling in N-hour horizon. Recommendation only; v3.13.0 emits no
external action via this runbook.

## Prerequisites

| Prerequisite | Source | Notes |
|---|---|---|
| `ProfileConfig` cottage variant | `SCH-009` | `profile_kind=cottage` or `remote_dwelling`, climate_zone, country |
| Public weather feed access | `ADAPT-004` rest_api (Open-Meteo, MET, NOAA, ECMWF) | No auth for public feeds |
| Building thermal model | `ADAPT-001` document_corpus or ProfileConfig | Insulation R-value, thermal mass, square meters |
| Sensor history (synthetic OK for dry-run) | `StateHandle` (sqlite, internal) | Last 7 days hourly indoor/outdoor temp + humidity |

## Climate zone seed table

The dry-run supports 5 climate zones with reasonable defaults:

| Zone | Frost threshold | Insulation default | Notes |
|---|---|---|---|
| `nordic_inland` | < +2 C indoor / < -5 C outdoor sustained 6h | R-3 (typical wood cabin) | Finland, Sweden, Norway interior |
| `alpine` | < +5 C indoor / < -10 C outdoor | R-5 (mountain construction) | Swiss / Austrian / North Italian mountain |
| `mountain_us_canada` | < +5 C indoor / < -8 C outdoor | R-4 | Pacific Northwest, Rockies |
| `mediterranean_coastal` | < +3 C indoor / < 0 C outdoor | R-2 | Frost is rare; predictor still valuable |
| `arctic_subarctic` | < +1 C indoor / < -20 C outdoor | R-7 (heavy insulation) | Lapland, Yukon, Siberia |

Custom climate zones can be added via `ProfileConfig`.

## Step-by-step

### Step 1: ProfileConfig

```yaml
schema_version: 1
profile_id: cottage_demo
profile_kind: cottage
country: FI                            # example
timezone: Europe/Helsinki
language: fi
currency: EUR
climate_zone: nordic_inland
service_provider_refs:
  - provider:open_meteo_public
data_residency_policy: policy:eu_only
external_write_policy_ref: policy:cottage_no_external_writes
redaction_policy_ref: policy:cottage_default_redaction
default_risk_policy: external_effect_requires_rco
operator_review_required: true
credential_vault_impl: os_keyring        # no creds in dry-run
                                          # but field is required
```

### Step 2: DocIngest (manual for v3.13.0; automated in Sprint 2+)

Operator drops inputs into a local directory (e.g.
`iterations/anchor_use_case/dry_run/cottage/inputs/`):
- Building thermal model parameters (YAML or PDF excerpt)
- Synthetic sensor history (CSV, 7 days, indoor/outdoor T+RH)
- ProfileConfig YAML

For v3.13.0 dry-run, the parser is operator-invoked; in Sprint 2+
DocIngest reads and emits a `solver_candidate_proposal` event.

### Step 3: SolverCandidateManifest assembly

```python
SolverCandidateManifest(
    candidate_id="frost_risk_predictor_cottage_demo_001",
    source_docs=["doc:thermal_model_yaml", "doc:sensor_history_csv"],
    source_tools=[],
    training_contracts=["ctr_date", "ctr_vector", "ctr_memory"],
    state_handles=["state:weather_forecast_cache",
                    "state:sensor_history",
                    "state:frost_risk_predictions"],
    connector_handles=["conn:weather_forecast_public"],
    shadow_inputs=["synth_cold_snap_24h",
                    "synth_thaw_24h",
                    "synth_steady_freeze_72h"],
    shadow_expected_outputs=[
        "high_risk_alert_within_6h",
        "no_risk_within_24h",
        "medium_risk_within_72h",
    ],
    divergence_score=None,
    accepted_differences=[],
    rejected_differences=[],
    promotion_decision="awaiting_shadow",
    rollback_plan="recovery:frost_predictor_v1",
    operator_review_id="op_review_002",
    provenance_signatures=[],
    activation_state="unactivated",
)
```

This example manifest is validated against
`schemas/v3_13_0/solver_candidate_manifest.schema.json` in
`tests/contracts/test_runbook_examples_v3_13_0.py` so the runbook
cannot silently drift from the schema.

### Step 4: Shadow run

```bash
python -m waggledance.core.v3_13_0.shadow_runner \
  --candidate frost_risk_predictor_cottage_demo_001 \
  --profile-config cottage_demo \
  --input synth_cold_snap_24h \
  --output-state state:dry_run_cottage_coldsnap
```

Expected:
- Prediction issued for the synthetic 24h cold snap
- Risk level + horizon hours documented in output
- `shadow.run_completed` MAGMA event

### Step 5: DivergenceAnalyzer

For `frost_risk_predictor` template family:
- `forecast_within_confidence_interval` -> `noise`
- `forecast_outside_ci` -> `material`
- `different_risk_class` -> `critical`

Expected divergence score `< 0.05` for synthetic dry-run.

### Step 6: Solver-RCO + provenance signing

Same bridge protocol as HOME runbook:
- Owner emits `handoff/rco_requested` with `payload.kind = solver`
- Peer responds `handoff/rco_pass` (or `changes_requested`)
- Both signatures recorded in MAGMA

### Step 7: Operator review

For COTTAGE dry-run, operator inspects:
- Did the predictor identify the cold snap correctly?
- Is the risk horizon plausible (6h-72h windows)?
- Does the prediction account for building thermal mass?
- Are climate-zone defaults sensible for the chosen zone?

**No external action triggers in v3.13.0 dry-run.**

### Step 8: Edge-AI consideration (Sprint 4+ forward-pointer)

COTTAGE deployments typically have intermittent connectivity. The
runbook flags this as a future consideration:
- For autonomous mode in Sprint 4+, the solver must run on local
  hardware with cached weather forecast
- Cached forecast freshness contract: max 12h stale before
  predictions are marked low-confidence
- Local LLM use (per `DEF-001` multilingual-e5-small) instead of
  cloud-based reasoning

This is OUT of scope for the v3.13.0 dry-run runbook but explicitly
noted so the design path is preserved.

## Stop conditions

- `ANTI-001`: unbounded weather feed query
- `ANTI-006`: feed rate limit exceeded
- `WRT-003`: any attempt to control cottage heating from
  v3.13.0 dry-run -- blocked
- Operator scope policy denies the run
- Climate zone unsupported and no custom override

## Acceptance criteria

A fresh v3.13.0 install can:
1. Load cottage `ProfileConfig` with `climate_zone`
2. Parse synthetic thermal model + sensor history (Sprint 2 DocIngest
   or manual for v3.13.0)
3. Construct a `SolverCandidateManifest`
4. Run shadow against 3 weather scenarios
5. Score divergence per scenario
6. Complete the solver-RCO handshake
7. Display predictions + risk classification to operator

No real cottage data is required.
