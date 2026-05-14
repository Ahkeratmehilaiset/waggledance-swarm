# ENG-01 spot_electricity_monitor -- first slice pilot

**Case id**: `ENG-01__spot_electricity_monitor__home` (from
`tests/fixtures/v3_13_0/operator_case_seed_bundle.json`, PR #388, main).
**First slice**: `fetch_next_24h_spot_prices_and_return_top_3_cheapest_hours`.
**Shadow fixture**: `tests/fixtures/v3_13_0/eng01_spot_electricity_shadow.json`.
**Risk class**: informational (read-only advisory output).
**Operator profile**: home.

## Purpose

This is the first concrete release-pipeline slice that targets
*operator-facing* value rather than substrate hardening. The slice
answers exactly one question for the operator: *"which are the 3
cheapest hours to use electricity in the next 24 hours?"*

If this slice ships and one operator uses it once at the mokki to time
their water-boiler or car-charger, that is the project's first
delivered operator-facing value. Everything upstream of that single
delivery is substrate; this pilot is the path to actually using the
substrate.

## Why ENG-01 first

* **Risk class**: `informational`. Read-only advisory output; no
  external_effect; no operator approval needed per WriteRCOGate.
* **Connector dependencies**: the operator's existing pattern uses
  `helen_browser_session` (private, requires credentials) AND the
  public `fingrid_spot_price_public_feed` (no credentials). The first
  slice uses ONLY the public feed -- no credential dependency.
* **First-slice scope**: a list of 3 hours. Easy to verify the output
  by hand against any spot-price source the operator already trusts.
* **Synthetic shadow exists**: the seed bundle entry already named
  `synthetic_24h_winter_with_known_min_at_02_00_local` as the shadow
  expected output, so the fixture shape was pre-decided. This pilot
  delivers the deterministic fixture.
* **Operator-facing value visible in one sentence**: "halvimmat
  tunnit seuraavan 24h aikana ovat 02:00, 01:00, 03:00 UTC". Anyone can
  read that and act on it without further training.

## How it maps to the existing v3.13.0 substrate

The first slice flows end-to-end through the substrate that the e2e
smoke harness in `tests/v3_13_0/test_e2e_solver_rco_smoke.py` (PR #387)
already exercises. Stage-by-stage mapping:

| Stage                                  | Module / hook                                                      | What the first slice supplies                                                                                                                |
|----------------------------------------|--------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| 1. operator input dir                  | `DocIngest.build_doc_ingest_proposal`                              | A HOME `profile_config.yaml` + a placeholder `tariff_structure.md` + a placeholder `consumption_sample.csv` (synthetic; production: operator's own files). |
| 2. candidate manifest                  | hand-crafted SCH-005 manifest (production: `SolverSynthesizer`)    | Manifest with `candidate_id="eng01_spot_electricity_monitor_home_v1"`, `target_domain="DOM-007"` (utility / energy), `target_write_risk="informational"`. |
| 3. signing                             | `SolverProvenance.sign(owner=claude) + sign(peer=codex)`            | Mutual-RCO signing pattern. Activation state advances to `SIGNED`.                                                                            |
| 4. shadow run                          | `ShadowRunner.run` with the shadow fixture as the baseline input  | Each scenario in the fixture (`happy_path__synthetic_24h_winter`, `happy_path__synthetic_24h_summer_with_low_spread`, plus 3 failure modes). |
| 5. divergence comparison               | `DivergenceAnalyzer.compare_json`                                 | The candidate solver's top-3 ranking compared against `expected_output.top_3_cheapest_hours_utc` from the fixture.                              |
| 6. activate                            | `SolverProvenance.activate` (after signed + divergence below threshold) | Activation state advances to `ACTIVATED`. Substrate-recorded MAGMA `solver.activation_authorised` audit event.                                 |
| 7. WriteRCOGate route                  | `WriteRCOGate.route` on an `informational` write intent           | The advisory output (top-3 hours + recommendation text) is written to a local artifact for the operator to read. Risk class `informational`; no peer-RCO required by the gate (informational path is the lightest). |
| 8. execute                             | `WriteRCOGate.execute`                                            | Writes the advisory record. Substrate-recorded MAGMA `write.effect_completed` audit event.                                                    |

## Shadow fixture structure

`tests/fixtures/v3_13_0/eng01_spot_electricity_shadow.json` carries 5
deterministic scenarios:

* **happy_path__synthetic_24h_winter_with_known_min_at_02_00_local** --
  Winter price curve with a known trough 01:00-04:00 UTC. Top-3 = 02:00,
  01:00, 03:00 UTC with prices 0.031, 0.038, 0.042 EUR/kWh. The shadow
  fixture for the seed bundle's `synthetic_24h_winter_with_known_min_at_02_00_local`
  named in PR #388.
* **happy_path__synthetic_24h_summer_with_low_spread** -- Summer price
  curve with narrower spread. Top-3 = 02:00, 03:00, 01:00 UTC. Tests
  that the first slice still produces a stable ranking when spread is
  small.
* **failure_mode__stale_data_refused** -- Feed fetched 18h before
  horizon start (threshold 12h). The slice must refuse rather than ship
  stale advice. Maps to `recommendation_freshness_ttl_minutes` guard.
* **failure_mode__missing_hour_refused** -- A gap at 03:00 UTC in the
  24-hour window. The slice must refuse rather than silently produce a
  top-3 over partial data.
* **failure_mode__non_monotonic_horizon_refused** -- Duplicate hour
  02:00 UTC in the feed. The slice must refuse rather than silently
  dedupe.

All scenarios share `writerco_risk_class: informational` -- failures
are upstream-data signals, not substrate invariant violations.

## What is NOT in this pilot

* **No live Fingrid network connector**. The pilot consumes the
  shadow fixture only. Production wiring (HTTP fetch + parse) is a
  separate Sprint 2+ deliverable.
* **No operator CLI**. The pilot does not add a `python -m
  waggledance.solver.eng01` entry point. Operator invocation in the
  pilot is the e2e smoke harness extension (test code path).
* **No SolverSynthesizer**. The candidate manifest in the smoke harness
  is hand-crafted from the seed bundle entry. A real SolverSynthesizer
  pass would generate it from the seed bundle + the runbook context;
  that is the immediate next deliverable AFTER the first slice runs
  end-to-end with a hand-crafted manifest.
* **No operator-facing UI / dashboard**. The advisory output is a
  written local artifact; production rendering belongs to the
  SituationRoom Sprint 2 deliverable.
* **No recommendation execution**. The first slice STOPS at "here are
  the 3 cheapest hours"; it does NOT automatically schedule a water
  boiler, car charger, or anything else. Automation would require a
  `external_effect` risk class write through `WriteRCOGate`, which is
  out of scope for the first slice.

## Suggested next concrete step (after this PR lands)

Extend `tests/v3_13_0/test_e2e_solver_rco_smoke.py` (the e2e smoke
harness merged in PR #387) with a new test
`test_e2e_eng01_first_slice_with_shadow_fixture` that:

1. Loads the case `ENG-01__spot_electricity_monitor__home` from the
   seed bundle (`tests/fixtures/v3_13_0/operator_case_seed_bundle.json`,
   in main).
2. Loads the shadow fixture's `happy_path__synthetic_24h_winter`
   scenario from `tests/fixtures/v3_13_0/eng01_spot_electricity_shadow.json`
   (this PR).
3. Hand-crafts a SCH-005 manifest using the case + a synthetic
   candidate_id.
4. Drives all 8 stages from the mapping table above.
5. Asserts that the candidate solver's top-3 output matches the
   fixture's `expected_output.top_3_cheapest_hours_utc` exactly.
6. Repeats for the 3 failure-mode scenarios, asserting refusal markers.

That extension is tests-only (uses the existing substrate). It is the
SMALLEST viable step toward "operator gets a real recommendation from
a real-shape solver", short of actually shipping the Fingrid connector.

## Operator delivery checklist (when this pilot is fully wired)

A future session can check off:

* [x] Seed bundle case ENG-01 in main (PR #388, this PR's parent).
* [x] Shadow fixture in main (this PR).
* [x] Pilot doc in main (this PR).
* [ ] e2e smoke test extension consuming the fixture (next PR after
      this lands; suggested step above).
* [ ] SolverSynthesizer v1 that turns the seed bundle entry into the
      manifest automatically (Sprint 2).
* [ ] Real Fingrid public-feed HTTP connector (Sprint 2).
* [ ] Operator CLI / SituationRoom render of the advisory (Sprint 2+).
* [ ] One operator session at mokki where the operator reads the
      advisory and acts on it. (First true operator-facing value
      delivery.)

The checklist gives the operator a visible roadmap from "substrate
exists" to "operator gets value", with this pilot landing the first 3
items in a single release-pipeline PR.

## Authority

Codex `handoff/assigned_prepare_then_write_after_pr388` 2026-05-14
T09:47:42Z on `claude-release-eng01-first-slice-pilot-2026-05-14`. PR
#388 merged at 09:49:07Z (`fbadb87` on main) per Codex
`done/merged` (implicit) and `decision/rco_pass` 09:49:00Z. Write scope
honored: exactly `docs/operator_cases/v3_13_0_eng01_first_slice_pilot.md`
and `tests/fixtures/v3_13_0/eng01_spot_electricity_shadow.json`.
