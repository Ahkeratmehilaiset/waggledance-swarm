# v3.13.0 Multi-Case Session Handoff (2026-05-15)

**Operator-readable + agent-readable.** If you restart the Claude or
Codex agents (e.g., after travel from / to the mokki / cottage), read
this file first. It records what shipped today, what is open, and how
to resume.

**Main HEAD at session close:** `aa6cef1` was the post-PR #407 state;
post-PR #409 (CLI adapter flags) the operator-CLI seam for ENG-06 is
complete. Read `git log -10 origin/main` for the exact tip.

## Yhden minuutin yhteenveto

23 release-pipeline PR:aa shipattu mainiin kahden vuorokauden aikana
2026-05-14 -- 2026-05-15. Kolme operaattori-casea end-to-end-ajettavissa:
ENG-01 sahkon halvimmat tunnit, FIN-10 kuittien luokittelu cottage vs
home, ENG-06 takan 30-paivan yhteenveto. **Operaattori on ajanut WD:n
ensimmaista kertaa mokilla** 2026-05-15T13:04Z; ENG-06 toimi
synteettisella sample-datalla. Sitten operaattori paljasti laajemman
vision: WD:n solverien pitaa pystya yhdistymaan automaattisesti kenen
tahansa ymparistoon. Tama on substantiaalinen substraatti-laajennus,
ei pelkka seuraava case. Strateginen scout-keskustelu Codexin kanssa
on auki tata vision-kysymysta varten; Claude jatti scoutin ja Codex
arvioi 1-2 tunnin sisalla.

**12 scout->implementation roundtrip-kierrosta talla istunnolla.**
Kaikki RCO PASS, 0 loydosta, kaikki CI 5/5. Consult-Codex-first malli
toimi laajalla mittakaavalla. Substraatti on tuotantotasoinen mutta
ei vahvistettu reaalimaailmassa kuin yhdella synteettisella ajolla.

## What works for the operator RIGHT NOW

Three CLI commands ship today, all consume operator-supplied JSON
files:

```powershell
# ENG-01: cheapest 3 hours from a public spot-price feed
python -m waggledance.adapters.cli.eng01_recommend --input examples/eng01/offline_prices_sample.json --pretty
python -m waggledance.adapters.cli.eng01_recommend --url https://prices.example.test/today --rows-path data,prices --hour-key timestamp_utc --price-key eur_per_mwh --pretty

# FIN-10: classify receipts cottage vs home
python -m waggledance.adapters.cli.fin10_classify_receipts --input examples/fin10/receipts_sample.json --pretty

# ENG-06: 30-day cottage burn-log summary
python -m waggledance.adapters.cli.eng06_fireplace --input examples/eng06/burn_log_sample.json --pretty
# operator-friendly key names + Fahrenheit/Kelvin temps:
python -m waggledance.adapters.cli.eng06_fireplace --input my_takka_export.json --day-key date --fire-count-key burns --temp-unit fahrenheit
```

ENG-01 also exposes an HTTP route for SituationRoom dashboards:

```powershell
# write atomic snapshot from CLI to data/eng01/latest_advisory.json:
python -m waggledance.adapters.cli.eng01_recommend --url https://prices.example.test/today --render-card --output latest_advisory.json
# serve to dashboard:
curl http://localhost:8000/api/eng01/advisory/latest
```

## PRs shipped this session (chronological)

The session ran ~2026-05-14T07:49Z through 2026-05-15T15:28Z.
Numbering follows the cluster's release-pipeline track.

| PR  | Title                                                      | Type     |
|-----|------------------------------------------------------------|----------|
| 387 | e2e harness scaffolding with mocked candidates             | substr.  |
| 388 | 18-case operator seed bundle                                | substr.  |
| 389 | SolverSynthesizer (seed-entry -> SCH-005 manifest)         | substr.  |
| 390 | ENG-01 shadow fixture (5 scenarios)                         | substr.  |
| 391 | e2e harness extension consuming seed bundle + fixture       | substr.  |
| 392 | ENG-01 solver core (`recommend_top_3_cheapest_hours`)       | ENG-01   |
| 393 | ENG-01 core fail-closed hardening (bool rejection)          | ENG-01   |
| 394 | ENG-01 provider-neutral price feed adapter                  | ENG-01   |
| 395 | ENG-01 offline CLI                                          | ENG-01   |
| 396 | Substrate: classify informational advisory artifacts        | substr.  |
| 397 | ENG-01 provider-neutral CLI sample + provider-neutral docs  | ENG-01   |
| 398 | ENG-01 response parser (provider-neutral JSON)              | ENG-01   |
| 399 | ENG-01 injectable HTTP transport (15 fail-closed markers)   | ENG-01   |
| 400 | ENG-01 CLI URL mode (live HTTP fetch via transport)         | ENG-01   |
| 401 | ENG-01 feed-selection docs                                  | ENG-01   |
| 402 | ENG-01 advisory card + read-only snapshot route             | ENG-01   |
| 403 | ENG-01 CLI atomic --output writer                           | ENG-01   |
| 404 | ENG-01 output-snapshot docs                                 | ENG-01   |
| 405 | FIN-10 receipt classifier (cottage vs home)                 | FIN-10   |
| 406 | ENG-06 fireplace solver core                                | ENG-06   |
| 407 | ENG-06 offline CLI + sample                                 | ENG-06   |
| 408 | ENG-06 burn-log feed adapter (C/F/K + date-only support)    | ENG-06   |
| 409 | ENG-06 CLI adapter flags (--day-key, --temp-unit, etc.)     | ENG-06   |

23 PRs total. Each one merged via mutual-RCO with 0 findings.
CI 5/5 SUCCESS on every PR. Audit count holds at 49 MAGMA event types
across the whole cluster.

## Knowledge layer (NOT directly tied to these PRs but loadbearing)

`knowledge/` directory contains **75 personas** with `core.yaml`
schemas. Each persona has:

* `header` -- agent_id, name, version
* `ASSUMPTIONS`
* `DECISION_METRICS_AND_THRESHOLDS` -- e.g.,
  `knowledge/air_quality/core.yaml` has `pm25_ug` WHO 15 ug/m3,
  `co_ppm_indoor` <9 ppm, `radon_bq` 200 Bq/m3, `pollen_birch` >80 etc.
* `SEASONAL_RULES`
* `FAILURE_MODES`
* `PROCESS_FLOWS`
* `KNOWLEDGE_TABLES`
* `COMPLIANCE_AND_LEGAL` -- e.g., STM 1044/2018 radon, jatelaki avopoltto
* `UNCERTAINTY_NOTES`
* `SOURCE_REGISTRY` -- HSY/SYKE, THL, STUK citations
* `eval_questions`

The cottage-relevant subset includes (non-exhaustive):
air_quality, beekeeper, chimney_sweep, electrician, firewood,
fire_officer, frost_soil, hive_security, hive_temperature, hvac_specialist,
ice_specialist, sauna_master, septic_manager, smart_home, well_water,
wilderness_chef, yard_guard.

**This knowledge is not yet exposed to any solver.** No PR in this
session loads `core.yaml` thresholds into the substrate. This is one
of the largest untapped operator-value reservoirs in the project.

## Open work at session close

### Current bridge state

* PR #409 merged at `2026-05-15T15:28:06Z`.
* New scout assigned to Claude at `2026-05-15T15:28:07Z`:
  `claude-scout-next-operator-value-after-pr409-2026-05-15`.
* Operator (Jani) revealed strategic redirect at ~`15:35Z`:
  "WD:n solverien pitaa pystya yhdistymaan automaattisesti kenen
  tahansa ymparistoon" -- WD solvers must auto-connect in anyone's
  environment.
* Claude posted scout response at `~16:00Z` with explicit ask to
  Codex for strategic analysis on the auto-connect vision.
* **Codex strategic analysis -- PENDING** at session close.
* Operator leaving mokki within 1 hour of close. This handoff doc is
  the entry point for the next agent session.

### Operator-validation moment (FIRST in project history)

`2026-05-15T13:04Z` -- Operator ran ENG-06 CLI for real at the
cottage:
```
python -m waggledance.adapters.cli.eng06_fireplace --input examples/eng06/burn_log_sample.json --pretty
```
Output: `result_marker=OK`, 4 fire events over 3 days, avg chimney
89.6 C, peak 176.8 C, horizon 2026-01-01 -- 2026-01-30.

This was the first time any WD module ran in operator-validated
production-shape mode. It worked on synthetic data; real burn-log
data has not been ingested yet.

### Operator-revealed gaps

The operator asked whether WD sees the Digheran indoor-air analyzer on
the cottage LAN. Answer: **NO**. PR #399 transport actively refuses
RFC1918 / loopback hosts:
* `URL_LOCAL_HOST_REFUSED`, `URL_PRIVATE_HOST_REFUSED`.

This was Claude's own scout recommendation as SSRF defense. For
operator-on-own-LAN use, the defense is too strict. A `--allow-private-host`
opt-in flag (analog to existing `--allow-credential-headers`) is the
smallest unblock.

Additionally: no device-discovery substrate exists. No mDNS, no SSDP,
no Bonjour, no Modbus scanner. WD cannot find devices; the operator
must hand-supply `--url`.

## Next-session entry points (RANKED for next agent)

### Lane 1 (RECOMMENDED): wait for Codex strategic response, then act

1. Open `.agent-bridge/shared/events.jsonl` and tail for events on
   task_id `claude-scout-next-operator-value-after-pr409-2026-05-15`.
2. Codex should have posted a strategic analysis bridge message by
   then. Read it.
3. If Codex recommends the LAN-bridge AIR-01 lane:
   * Sub-scout the smallest `--allow-private-host` opt-in PR
     (transport extension only, ~40 LoC + tests).
   * Then sub-scout AIR-01 first slice consuming
     `knowledge/air_quality/core.yaml` thresholds.
4. If Codex recommends the discovery substrate instead:
   * Sub-scout the smallest mDNS-only first PR
     (probably zeroconf library; pure-logic discoverer that returns
     a list of `(host, port, service_type)` tuples; opt-in flag).

### Lane 2: continue ENG-06 staircase if Codex defers strategic question

Per Claude's earlier scout, remaining ENG-06 follow-ups in order:
* SQLite local takka-history adapter (analog of PR #394).
* ENG-06 advisory card formatter (analog of PR #402's card).
* ENG-06 snapshot route (analog of PR #402's route).
* ENG-06 CLI --output writer (analog of PR #403).
* ENG-06 docs.
* ENG-06 weather forecast integration (deferred per
  `no_forecast` suffix in first slice).

### Lane 3: third case from seed bundle

If operator wants a NEW case before completing ENG-06: per Claude's
ranking 2026-05-15T11:29Z:
* Rank 2 INS-01 insurance_offer_comparator (home, PDF first slice).
* Rank 3 BEE-01 bee_apiary_tracker (cottage+home, SQL date-filter list).

### Lane 4 (DO NOT PROPOSE WITHOUT CODEX SIGN-OFF)

* Stable v3.13.0 release tag. Per CLAUDE.md rule 10, atomic-flip is
  a separate operator-signed event, not a developer decision.
* Direct push to main. Per CLAUDE.md rule 6, all merges go through PR.

## Operator-facing TODO when operator returns

* Run `python -m waggledance.adapters.cli.eng06_fireplace --input examples/eng06/burn_log_sample.json --pretty` at cottage. Confirm output is readable.
* If you have a real takka log (Excel, paper notes, sensor export),
  shape it into the burn-log JSON format and try `--day-key date --fire-count-key burns --temp-unit fahrenheit` if you used different keys.
* Tell next-session Claude: what was awkward? What was useful? What's
  the actual format your Digheran air analyzer exposes (REST endpoint
  shape, JSON keys, units)?
* Decide: do you want the LAN-bridge AIR-01 lane prioritized (auto-
  connect vision), or the SQLite ENG-06 adapter (real takka data),
  or a third case (INS-01 insurance offers / BEE-01 apiary)?

## Bridge / coordinator state

* All 23 PRs landed via consult-Codex-first model. Claude scouts;
  Codex implements; Claude RCO-verifies; Codex merges via
  `gh pr merge --match-head-commit=<EXPECTED_HEAD>` per CLAUDE.md
  rule 9.
* No autonomous merges to main. No `--admin`, no `--no-verify`, no
  force-push.
* Operator's bridge consensus discipline holds: routine forward
  progress moves under Claude+Codex; only 5 escalation categories
  reach operator (destructive fs/git, credentials, external payment,
  unresolved write-scope conflict, legally/security sensitive).
  Today's auto-connect vision = strategic redirect, falls under
  consult-Codex-first.
* Release gate remains HOLD. No stable tag, no version bump. Per
  CLAUDE.md rule 10, the atomic-flip needs operator-signed
  `HUMAN_APPROVAL_V2.yaml`. No such draft authored.

## Files / paths the next agent needs

* `.agent-bridge/shared/events.jsonl` -- the coordinator bridge log
* `iterations/anchor_use_case/sprint_1/claude_lane/` -- Sprint 1
  artifacts (gitignored locally, but referenced in Codex's RCOs)
* `tests/fixtures/v3_13_0/operator_case_seed_bundle.json` -- the
  18-case operator catalog
* `examples/eng01/offline_prices_sample.json` -- shipped sample
* `examples/eng06/burn_log_sample.json` -- shipped sample
* `examples/fin10/receipts_sample.json` -- shipped sample
* `knowledge/` -- 75 personas with curated thresholds (UNUSED by
  solvers; the largest operator-value reservoir not yet tapped)
* `docs/operator_cases/v3_13_0_eng01_feed_selection_guide.md` --
  operator-friendly feed-wiring guide
* `docs/operator_cases/v3_13_0_eng01_first_slice_pilot.md` --
  ENG-01 cluster narrative
* `docs/operator_cases/v3_13_0_capability_seed_bundle.md` --
  18-case capability overview

## How to resume

1. `git fetch origin && git checkout main && git pull --ff-only`
2. `tail -20 .agent-bridge/shared/events.jsonl` to see the latest
   bridge events. Codex may have already posted strategic analysis
   on task `claude-scout-next-operator-value-after-pr409-2026-05-15`.
3. If yes -- read his analysis; pick the lane he recommends; sub-scout
   the smallest first PR.
4. If no -- post a wake_request to Codex asking for the strategic
   analysis (the operator explicitly asked for it).
5. Either way, keep release gate HOLD. No tag, no version bump.

## Authority

* Operator (Jani Korpi) request 2026-05-15T~15:35Z verbatim:
  "pyyda codexia analysoimaan ja antaamaan oma mielipide tedaan
  silleen ym. kaikki valmiiksi niin etta wd:n solverien pystyy
  connektoimaan automaattisesti kenenka tahansa ymparistossa. Olen
  lahossa kohta mokilta Kyele valmistelut codexilta ja kijoittakaa
  seuraavalle sessiolle ohjeet jotta osaavat jatkaa siita mihinka
  jaatiin."
* This doc is Claude's response to that request. The next agent
  reads this doc first.

---

End of v3.13.0 multi-case session handoff.
