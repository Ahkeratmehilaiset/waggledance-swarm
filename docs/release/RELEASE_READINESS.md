# Release Readiness — WaggleDance (Phase 16A / v3.7.5-upstream-runtime-alpha)

**Status:** alpha / prerelease only. This document defines what `release-ready` means in the WaggleDance autonomy lineage, what version-tag policy applies, and which gates must pass before a stable release is justified.

`release-ready` here means **truthful alpha/prerelease readiness**: a reproducible proof, green CI, clear docs, Docker truth, known limitations documented, and no stable production claim.

It does **not** mean stable v3.8.0 unless a separate stable release gate explicitly proves production readiness (see "Tag versioning policy" below).

## Latest tag — v3.7.4-runtime-hint-alpha (Phase 15)

Phase 15 prerelease tag on `origin/main` at merge commit `2b9978d` (PR #62). Adds automatic deterministic hint derivation in `AutonomyRuntime.handle_query`; live before/after proof through the production caller; provider/builder delta = 0 in proof.

## Recommended next tag — v3.7.5-upstream-runtime-alpha (Phase 16A)

Created **only after** the Phase 16A PR merges and the post-merge release docs check passes.

* `v3.7.5-upstream-runtime-alpha` — Phase 16A alpha. Adds automatic deterministic upstream `structured_request` derivation at the `AutonomyService.handle_query` service-layer caller, lifting flat domain context (operation + flat fields) into the nested grammar Phase 15's runtime hint extractor consumes. Live before/after proof through the upstream caller's natural flat input shape; restart-continuity smoke proves persisted solvers and capability features survive control-plane close+reopen. Inner-loop provider/builder delta during proof = 0.

## Reproduce the latest proof from a clean clone

```
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
pip install -r requirements.lock.txt

# Targeted tests (subset matching this alpha's scope)
python -m pytest tests/autonomy_growth/ tests/storage/ tests/ui_hologram/ -q

# Phase 15 automatic runtime hint proof (still reproducible from main)
python tools/run_automatic_runtime_hint_proof.py

# Phase 16A automatic upstream structured_request proof
python tools/run_upstream_structured_request_proof.py
```

Expected from Phase 15 proof:

* `corpus_total = 98`
* `manual_low_risk_hint_in_input_detected = false`
* `proof_constructed_runtime_query_objects = false`
* `before.served_total = 0`
* `after.served_via_capability_lookup_total = 98`
* `kpis.provider_jobs_delta_during_proof = 0`
* `kpis.builder_jobs_delta_during_proof = 0`

Expected from Phase 16A proof:

* `corpus_total = 98`
* `selected_upstream_caller = waggledance.application.services.autonomy_service.AutonomyService.handle_query`
* `manual_structured_request_in_input_detected = false`
* `manual_low_risk_hint_in_input_detected = false`
* `proof_constructed_runtime_query_objects = false`
* `proof_bypassed_selected_caller = false`
* `proof_bypassed_handle_query = false`
* `structured_request_derived_total = 98`
* `low_risk_hint_derived_total = 98`
* `before.served_total = 0`
* `after.served_via_capability_lookup_total = 98`
* `negative_cases_passed_total = 7 / 7`
* `kpis.provider_jobs_delta_during_proof = 0`
* `kpis.builder_jobs_delta_during_proof = 0`

Each command above is **tested in this session**.

## Restart continuity smoke

Phase 16A also ships a restart-continuity smoke test:

```
python -m pytest tests/autonomy_growth/test_upstream_restart_continuity.py -q
```

Proves that auto-promoted solvers and capability-feature rows survive a control-plane SQLite close+reopen cycle, and that the same flat upstream input continues to be served via capability lookup with `provider_jobs_delta = builder_jobs_delta = 0` across the restart. **Tested in this session.**

## Reproduce inside Docker

See `docs/deployment/DOCKER_QUICKSTART.md`. Docker steps are documented but **not tested in this session** — verify on your own machine.

## CI status

* Workflows: `Tests` (unified) + `WaggleDance CI` (test 3.11 / 3.12 / 3.13 + security-scan).
* Phase 14 PR #61 last green run on these workflows. Phase 15 PR #62 went green at merge commit `2b9978d`. Phase 16A PR re-runs them on its head SHA.

## Docker status

| concern | status |
|---|---|
| `Dockerfile` present | yes (inherited; not modified by Phase 15) |
| `docker-compose.yml` present | yes (inherited) |
| build verified in this session | no — Docker not available locally |
| run verified in this session | no — Docker not available locally |
| autonomy proof inside Docker verified | no |

The Docker layer is documented as a contract in `DOCKER_QUICKSTART.md`. An external operator with Docker installed should verify before treating it as production.

## Docs status

| doc | updated in Phase 15 | truth |
|---|---|---|
| `README.md` (top section) | yes — concise alpha summary at the top | reflects Phase 15 shipped truth |
| `CURRENT_STATUS.md` | yes — Phase 15 section above Phase 14 | reflects Phase 15 shipped truth |
| `CURRENT_STATE.md` | not regenerated by Phase 15 (see CURRENT_STATE rule) | last regenerated Phase 11 PR #57 |
| `CHANGELOG.md` | yes — Phase 15 block above Phase 14 | reflects Phase 15 shipped truth |
| `LICENSE-CORE.md` | yes — Phase 15 BUSL files added in same commit as code | up to date |
| `docs/architecture/RUNTIME_ENTRYPOINT_TRUTH_MAP.md` | new in Phase 15 | accurate |
| `docs/architecture/PROVIDER_PLANE_AND_BUILDER_LANES.md` | extended with Phase 15 paragraph | accurate |
| `docs/journal/2026-04-30_*` | unchanged historical | accurate |
| `docs/runs/phase15_runtime_hints_release_2026_05_01/automatic_runtime_hint_proof.md` | new | reproducible from `tools/` |
| `docs/runs/phase16_upstream_structured_request_2026_05_01/upstream_structured_request_proof.md` | new in Phase 16A | reproducible from `tools/` |
| `docs/runs/phase16_upstream_structured_request_2026_05_01/restart_continuity_smoke.md` | new in Phase 16A | reproducible from `tests/` |
| `docs/github/REPOSITORY_PRESENTATION.md` | new in Phase 15 | text prepared, not applied |
| `docs/deployment/DOCKER_QUICKSTART.md` | new in Phase 15 | tested/not-tested status documented |
| this file | extended in Phase 16A | self |

## Known limitations

* Auto-growth is bounded to six low-risk family kinds. Widening this allowlist requires a separate PR with code + tests + policy update; RULE 13 of every recent phase prompt forbids implicit widening.
* Capability-aware dispatch routes by structured features only. Per-cell / per-capsule routing is future work.
* No real Anthropic / OpenAI HTTP adapters. Only `dry_run_stub` and `claude_code_builder_lane` are exercisable end-to-end. The provider routing scaffold exists; the network adapters do not.
* No actuator-side autonomy. Auto-promoted solvers are *consultable*, not *authoritative*.
* The Stage-2 atomic flip is unchanged. `core/faiss_store.py:26 _DEFAULT_FAISS_DIR=data/faiss/`.
* Hot-path cache is in-process only; no shared cache across processes.
* No federation / multi-instance cluster work.
* P5 self-state snapshot and P6 episodic continuity (observability primitives) are deferred to Phase 16+.
* Phase 16A wires upstream `structured_request` propagation only at the `AutonomyService.handle_query` service-layer caller. HTTP / FastAPI route surface for query is not yet exposed (`/api/autonomy/query` does not exist; chat traffic uses a different lane).
* The `operation` selector at the upstream layer is restricted to the same six low-risk family kinds as Phase 15. No widening.
* Restart continuity is proven for the small in-process scratch DB used by the smoke test. Larger-scale durability (multi-GB DB, concurrent writers, crash mid-write) is not covered by this phase.

## Release blockers — for Phase 15 prerelease

None remaining. The Phase 15 release gate (P2 + P3 + P4 of the master prompt) is met:

* deterministic hint extractor exists (`runtime_hint_extractor.py`),
* the selected real caller is wired (`AutonomyRuntime.handle_query`),
* before/after proof goes through that caller's normal input shape,
* proof does not manually inject `low_risk_autonomy_query`,
* `provider_jobs_delta == 0`,
* `builder_jobs_delta == 0`.

## Release blockers — for Phase 16A prerelease

None remaining. The Phase 16A release gate (P2 + P3 + P4 + P5 of the master prompt) is met:

* deterministic upstream extractor exists (`upstream_structured_request_extractor.py`),
* the selected upstream caller is wired (`AutonomyService.handle_query`),
* before/after proof goes through that caller's natural flat input shape,
* proof does not manually inject `structured_request` or `low_risk_autonomy_query`,
* `structured_request_derived_total == low_risk_hint_derived_total == 98`,
* restart continuity proven (`tests/autonomy_growth/test_upstream_restart_continuity.py`),
* `provider_jobs_delta == 0`,
* `builder_jobs_delta == 0`.

## Release blockers — for hypothetical stable v3.8.0

These are **not addressed by Phase 15**. Listing them so the next release session knows what to clear:

* Docker tested end-to-end on at least one supported platform.
* External user can reproduce the latest proof from a fresh clone (some `pip install` paths are not yet documented for non-developer machines — needs a `pip install -e .` or wheel).
* README top section concise (≤ 10 lines summarising current state).
* No provider dependency in inner loop. ✓ already true (locked by tests).
* 100+ auto-promoted solvers in proof OR documented lower truthful corpus from real caller. ✓ Phase 14 + 15 both ship 98 (close to 100).
* Security audit or self-audit with documented findings.
* Stable release gate explicitly passed.

Until a separate session passes the stable gate, the **default tag for any Phase 15-ish work is prerelease only**.

## Tag versioning policy

| version range | what it means | gate |
|---|---|---|
| `v3.6.x` | substrate landed; no runtime behaviour change | Phase 10–11 substrate gate |
| `v3.7.x` | runtime-autonomy alpha increments | per-phase gate (Phase 11–16A+) |
| `v3.7.4-runtime-hint-alpha` (Phase 15) | automatic runtime hints + alpha release readiness | merged 2026-05-01 |
| **`v3.7.5-upstream-runtime-alpha`** (Phase 16A) | upstream `structured_request` propagation + restart continuity | this document |
| `v3.8.0` | first stable release | requires the stable gate above |
| `v4.0.0` | federation / multi-instance work | reserved; not before an explicit federation phase |

Any Phase 16A successor session producing more autonomy-lane improvements should use the next prerelease tag (e.g. `v3.7.6-...-alpha`) until the stable gate is explicitly passed.

## Cross-references

* Phase 15 release tag: `v3.7.4-runtime-hint-alpha`.
* Phase 14 release tag: `v3.7.3-hotpath-alpha`.
* Phase 13 release tag: `v3.7.2-runtime-harvest-alpha`.
* Phase 12 release tag: `v3.7.1-autoloop-alpha`.
* Phase 11 release tag: `v3.7.0-autogrowth-alpha`.
* Phase 10 substrate tag: `v3.6.1-substrate`.
* Phase 9 release tag: `v3.6.0`.
* `docs/runs/phase16_upstream_structured_request_2026_05_01/upstream_structured_request_proof.md` — Phase 16A proof artefact.
* `docs/runs/phase16_upstream_structured_request_2026_05_01/restart_continuity_smoke.md` — Phase 16A restart-continuity smoke.
* `docs/runs/phase15_runtime_hints_release_2026_05_01/automatic_runtime_hint_proof.md` — Phase 15 proof artefact (still reproducible from main).
* `docs/architecture/RUNTIME_ENTRYPOINT_TRUTH_MAP.md` — selected real callers (Phase 15 + Phase 16A).
* `docs/github/REPOSITORY_PRESENTATION.md` — external presentation text.
* `docs/deployment/DOCKER_QUICKSTART.md` — Docker contract + tested/not-tested status.
