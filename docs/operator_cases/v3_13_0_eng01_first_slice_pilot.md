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
* **Connector dependencies**: the first shipped path can consume either
  an already-fetched local spot-price JSON file or an operator-selected
  public HTTP JSON feed through the provider-neutral parser, transport,
  adapter, and solver. No provider catalog, credential store, or vendor
  support claim is built into ENG-01.
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

## Current boundaries

* **Operator-selected feed only**. The CLI accepts a URL chosen by the
  operator, but the project does not certify a real provider endpoint or
  include a provider catalog. The operator owns provider selection and
  field mapping.
* **Offline and URL operator CLI exist**. The offline invocation is:
  `python -m waggledance.adapters.cli.eng01_recommend --input examples/eng01/offline_prices_sample.json --pretty`.
  The URL invocation is:
  `python -m waggledance.adapters.cli.eng01_recommend --url https://prices.example.test/day-ahead.json --pretty`.
  The URL path uses the fail-closed HTTP transport and response parser;
  tests use injected transports and do not call live services.
* **No full dashboard UI**. The CLI can render a read-only advisory card
  and atomically write `data/eng01/latest_advisory.json`; the HTTP route
  `GET /api/eng01/advisory/latest` serves that operator-written JSON
  snapshot. A richer dashboard remains outside the first slice.
* **No recommendation execution**. The first slice STOPS at "here are
  the 3 cheapest hours"; it does NOT automatically schedule a water
  boiler, car charger, or anything else. Automation would require a
  `external_effect` risk class write through `WriteRCOGate`, which is
  out of scope for the first slice.

## Current operator invocation

Use the checked-in sample to exercise the shipped offline path:

```powershell
python -m waggledance.adapters.cli.eng01_recommend --input examples/eng01/offline_prices_sample.json --pretty
```

Expected top-3 hours from the sample are 02:00, 01:00, and 03:00 UTC.
The sample is synthetic and provider-neutral; it is shaped as
already-fetched hourly spot-price rows in EUR/MWh.

Use the URL mode when the operator has selected and verified a public
JSON feed:

```powershell
python -m waggledance.adapters.cli.eng01_recommend --url https://prices.example.test/day-ahead.json --pretty
```

Write the latest read-only advisory card for the HTTP snapshot route:

```powershell
python -m waggledance.adapters.cli.eng01_recommend `
  --url https://prices.example.test/day-ahead.json `
  --render-card `
  --pretty `
  --output data/eng01/latest_advisory.json
```

The snapshot can then be read from `GET /api/eng01/advisory/latest`.
The route only reads the operator-written file; it does not fetch a URL
or run the solver from the request handler.

For nested or differently named feed rows, pass `--rows-path`,
`--hour-key`, and `--price-key`. See
`docs/operator_cases/v3_13_0_eng01_feed_selection_guide.md` for the
operator feed-selection contract and refusal boundaries.

## Suggested next concrete step

Run one operator-selected public JSON feed through the URL mode, compare
the printed top-3 hours against the same provider's human-readable view,
and record the mapping (`--rows-path`, `--hour-key`, `--price-key`,
`--price-unit`) in the operator's local runbook. Keep credential/session
handling outside committed ENG-01 docs unless a future scoped provider
integration explicitly requires it.

## Operator delivery checklist (when this pilot is fully wired)

A future session can check off:

* [x] Seed bundle case ENG-01 in main (PR #388).
* [x] Shadow fixture in main (PR #390).
* [x] Pilot doc in main (PR #390).
* [x] SolverSynthesizer v1 for seed-to-manifest flow (PR #389).
* [x] e2e smoke test extension consuming the fixture (PR #391).
* [x] Solver core + fail-closed hardening (PR #392, PR #393).
* [x] Provider-neutral already-fetched feed adapter (PR #394).
* [x] Offline operator CLI + sample JSON (PR #395 and follow-up docs).
* [x] Provider-neutral HTTP response parser (PR #398).
* [x] Fail-closed HTTP transport for operator-selected public feeds
      (PR #399).
* [x] URL mode in the operator CLI (PR #400).
* [x] Operator feed-selection guide (PR #401).
* [x] Advisory card + read-only latest-advisory route (PR #402).
* [x] Atomic CLI `--output` snapshot writer (PR #403).
* [ ] Full dashboard UI beyond the JSON snapshot endpoint (Sprint 2+).
* [ ] One operator session at mokki where the operator reads the
      advisory and acts on it. (First true operator-facing value
      delivery.)

The checklist gives the operator a visible roadmap from "substrate
exists" to "operator gets value" and separates the shipped offline path
from the remaining live-provider and SituationRoom work.

## Authority

Codex `handoff/assigned_prepare_then_write_after_pr388` 2026-05-14
T09:47:42Z on `claude-release-eng01-first-slice-pilot-2026-05-14`. PR
#388 merged at 09:49:07Z (`fbadb87` on main) per Codex
`done/merged` (implicit) and `decision/rco_pass` 09:49:00Z. Write scope
honored: exactly `docs/operator_cases/v3_13_0_eng01_first_slice_pilot.md`
and `tests/fixtures/v3_13_0/eng01_spot_electricity_shadow.json`.

2026-05-15 follow-up: Codex updated this document and the ENG-01 fixtures to
use provider-neutral spot-price feed naming after the offline CLI and
provider-neutral adapter landed.

2026-05-15 follow-up: Codex updated this document after the provider-neutral
response parser, fail-closed HTTP transport, and CLI URL mode landed.

2026-05-15 follow-up: Codex updated this document after the advisory card,
read-only latest-advisory route, and atomic CLI `--output` writer landed.

After the feed-selection guide, advisory card, read-only route, and
atomic `--output` writer landed, the ENG-01 first slice is shippable
end-to-end as a provider-neutral advisory CLI plus JSON snapshot path.
The next delivery layer is one operator-run at mokki and any richer
dashboard/scheduling work, not another ENG-01 parser/transport/CLI step.
