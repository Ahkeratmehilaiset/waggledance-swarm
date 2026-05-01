# Release Readiness — WaggleDance (Phase 16B / v3.7.6-stabilization-alpha candidate)

**Status:** alpha / prerelease only. This document defines what `release-ready` means in the WaggleDance autonomy lineage, what version-tag policy applies, and which gates must pass before a stable release is justified.

`release-ready` here means **truthful alpha/prerelease readiness**: a reproducible proof, green CI, clear docs, Docker truth, known limitations documented, and no stable production claim.

It does **not** mean stable v3.8.0 unless a separate stable release gate explicitly proves production readiness (see "Tag versioning policy" below).

## Latest tag — v3.7.5-upstream-runtime-alpha (Phase 16A)

Phase 16A prerelease tag on `origin/main` at merge commit `d18e1da` (PR #63). Adds automatic deterministic upstream `structured_request` derivation at `AutonomyService.handle_query`; live before/after proof + restart-continuity smoke; provider/builder delta = 0 in proof.

## Recommended next tag — v3.7.6-stabilization-alpha (Phase 16B)

Created **only after** the Phase 16B PR merges and the post-merge release docs check passes.

* `v3.7.6-stabilization-alpha` — Phase 16B stabilization prerelease. Adds full-corpus restart continuity proof, proof-soak repeatability driver, +6 canonical seeds (+1 per low-risk family) bringing the corpus to 104 (≥ 100-solver release-gate threshold), bounded security self-audit, install-layering and HTTP-route stable-gate decisions, dependency / fresh-clone / Docker truth audit. Inner-loop provider/builder delta = 0.

Phase 16B is **not** a stable v3.8.0 release. The fail-closed stable gates documented below explain why v3.8.0 remains blocked.

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

# Phase 16B full-corpus restart continuity proof
python tools/run_full_restart_continuity_proof.py

# Phase 16B proof soak (5 iterations of each proof, ~9 min total)
python tools/run_phase16b_proof_soak.py --iterations 5
```

## Install-source layering

The repository carries three install sources, each with a different purpose. They are consistent given the layering — see `docs/runs/phase16b_stabilization_release_gate_2026_05_01/dependency_install_and_api_surface_truth.md` for the full breakdown.

| file | purpose | who uses it |
|---|---|---|
| `requirements.txt` | top-level / loose pin | developer local install; README quickstart |
| `requirements.lock.txt` | full lock file | Docker build; this document's reproduce-from-clone steps |
| `requirements-ci.txt` | minimal subset (no `faiss-cpu`, no Playwright) | GitHub Actions CI |

Expected from Phase 15 proof:

* `corpus_total = 98`
* `manual_low_risk_hint_in_input_detected = false`
* `proof_constructed_runtime_query_objects = false`
* `before.served_total = 0`
* `after.served_via_capability_lookup_total = 98`
* `kpis.provider_jobs_delta_during_proof = 0`
* `kpis.builder_jobs_delta_during_proof = 0`

Expected from Phase 16A proof (post-Phase-16B-P4 corpus):

* `corpus_total = 104` (Phase 16B P4 raised the canonical seed library from 98 to 104; ≥ 100 satisfies the v3.8.0 release-gate threshold for auto-promoted solvers)
* `selected_upstream_caller = waggledance.application.services.autonomy_service.AutonomyService.handle_query`
* `manual_structured_request_in_input_detected = false`
* `manual_low_risk_hint_in_input_detected = false`
* `proof_constructed_runtime_query_objects = false`
* `proof_bypassed_selected_caller = false`
* `proof_bypassed_handle_query = false`
* `structured_request_derived_total = 104`
* `low_risk_hint_derived_total = 104`
* `before.served_total = 0`
* `after.served_via_capability_lookup_total = 104`
* `negative_cases_passed_total = 7 / 7`
* `kpis.provider_jobs_delta_during_proof = 0`
* `kpis.builder_jobs_delta_during_proof = 0`
* `kpis.auto_promotions_total = 104`

Expected from Phase 16B full restart continuity proof:

* `corpus_total = 104`
* pre-restart pass-2 served via capability lookup = 104
* persisted solver count before/after reopen = 104 / 104 (identical)
* persisted `solver_capability_features` before/after reopen = 180 / 180 (identical)
* post-restart pass-2 served via capability lookup = 104
* every restart invariant true; `provider_jobs_delta_across_restart = 0`; `builder_jobs_delta_across_restart = 0`

Expected from Phase 16B proof soak (`--iterations 5`):

* 15 / 15 iterations pass (3 proofs × 5 iterations)
* no flakes
* per-iteration mean elapsed: ~33 s

Each command above is **tested in this session**.

## Restart continuity coverage

Phase 16A shipped a six-seed restart smoke; Phase 16B scales it to the full canonical corpus.

| level | scope | command |
|---|---|---|
| six-seed smoke | one seed per family | `python -m pytest tests/autonomy_growth/test_upstream_restart_continuity.py -q` |
| **full-corpus proof** | **all 104 seeds across all six families** | `python tools/run_full_restart_continuity_proof.py` |
| smoke locks proof JSON invariants | full proof artifact | `python -m pytest tests/autonomy_growth/test_full_restart_continuity_smoke.py -q` |

All three pass in this session.

## Reproduce inside Docker

See `docs/deployment/DOCKER_QUICKSTART.md`. Docker steps are documented but **not tested in this session** — verify on your own machine.

## CI status

* Workflows: `Tests` (unified) + `WaggleDance CI` (test 3.11 / 3.12 / 3.13 + security-scan).
* Phase 14 PR #61 last green. Phase 15 PR #62 green at merge commit `2b9978d`. Phase 16A PR #63 green at merge commit `d18e1da`. Phase 16B PR re-runs them on its head SHA.

## HTTP / API surface scope

**`v3.8.0` (and any equivalent stabilization tag) is a Python library / service-layer stable release, not an HTTP-API stable release.** Production callers integrate via `AutonomyService.handle_query(query, context, priority)`. The FastAPI surface for query-by-HTTP is not in scope for this release line.

Existing FastAPI `/api/autonomy/*` routes expose status, KPIs, learning-cycle control, proactive goals, and safety-case introspection only. There is no `/api/autonomy/query` route. Adding one would be a feature for a later phase, not a stabilization gate.

See `docs/runs/phase16b_stabilization_release_gate_2026_05_01/dependency_install_and_api_surface_truth.md` for the full HTTP / API stable-gate decision.

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
| `docs/runs/phase16b_stabilization_release_gate_2026_05_01/full_restart_continuity_proof.md` | new in Phase 16B | reproducible from `tools/` |
| `docs/runs/phase16b_stabilization_release_gate_2026_05_01/proof_soak_report.md` | new in Phase 16B | reproducible from `tools/` |
| `docs/runs/phase16b_stabilization_release_gate_2026_05_01/release_gate_100_solvers.md` | new in Phase 16B | seed-library expansion log |
| `docs/runs/phase16b_stabilization_release_gate_2026_05_01/fresh_clone_reproduction.md` | new in Phase 16B | local-clone done; remote/fresh deferred to P13.5 |
| `docs/runs/phase16b_stabilization_release_gate_2026_05_01/docker_verification.md` | new in Phase 16B | Docker not available in this session |
| `docs/runs/phase16b_stabilization_release_gate_2026_05_01/dependency_install_and_api_surface_truth.md` | new in Phase 16B | install layering + HTTP-route stable decision |
| `docs/runs/phase16b_stabilization_release_gate_2026_05_01/stable_gate_inventory.md` | new in Phase 16B | gate ledger MD |
| `docs/runs/phase16b_stabilization_release_gate_2026_05_01/stable_gate_inventory.json` | new in Phase 16B | machine-readable gate ledger |
| `docs/security/PHASE16B_SECURITY_SELF_AUDIT.md` | new in Phase 16B | security audit findings |
| `docs/github/REPOSITORY_PRESENTATION.md` | new in Phase 15 | text prepared, not applied |
| `docs/deployment/DOCKER_QUICKSTART.md` | extended in Phase 16B | tested/not-tested status documented |
| this file | extended in Phase 16B | self |

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
* restart continuity proven (`tests/autonomy_growth/test_upstream_restart_continuity.py`),
* `provider_jobs_delta == 0`,
* `builder_jobs_delta == 0`.

## Release blockers — for Phase 16B prerelease (`v3.7.6-stabilization-alpha`)

None remaining. The Phase 16B prerelease gate is met:

* full-corpus restart continuity proof passes (104 / 104 served before and after restart, persisted state identical across reopen, provider/builder delta 0 across restart).
* proof soak (5 iterations of each of three proofs) passes 15 / 15 with no flakes.
* canonical seed library raised from 98 to 104 (+1 per family, no allowlist widening); 104 ≥ 100 release-gate threshold.
* security self-audit completed (CI grep clean; manual grep on Phase 11+ files clean; `pip-audit` reports 32 dependency CVEs all classified low and not reachable from inner loop).
* install-source layering documented; HTTP / API surface stable scope documented.
* targeted tests green; provider/builder delta = 0 across all proofs.

## Release blockers — for stable v3.8.0 (still blocked)

The strict fail-closed rule for stable v3.8.0 requires every required-for-stable gate to PASS on the post-merge `main` SHA. Phase 16B leaves three blockers that prevent v3.8.0:

1. **g01 Docker end-to-end** — `docker` CLI is not installed in the Phase 16B development shell. No build, no in-container proof. `Dockerfile` and `docker-compose.yml` are inspected and unchanged; commands are documented in `DOCKER_QUICKSTART.md` as **not tested in this session**. An external operator with Docker installed must verify the documented steps before stable v3.8.0 is justified.
2. **g02 Remote/fresh-clone reproduction against post-merge main** — Phase 16B P5 verified a local-clone reproduction against the v3.7.5 SHA. Per the master prompt's `FRESH CLONE VERIFICATION RULE`, a remote/fresh-clone reproduction must run against `origin/main` after the Phase 16B PR merges. P13.5 will attempt this. If P13.5 is not executed against post-merge main, g02 remains blocked.
3. **g05 Security audit — `bandit` not installed** — `pip-audit` was available and ran successfully; `bandit` was unavailable and not installed in this session per the master-prompt rule about heavy new dependencies. There is no pre-existing stable-gate exception in this document for missing `bandit`; therefore the strict fail-closed rule blocks v3.8.0. An external operator must run `pip install bandit && bandit -r waggledance/ core/` and verify no high-severity findings before stable v3.8.0 is justified.

These blockers are explicit by design; Phase 16B's outcome is the prerelease `v3.7.6-stabilization-alpha`, which is a **success** outcome under stabilization-mode rules.

## Stable v3.8.0 — checklist as of Phase 16B

These are tracked here so the next release session knows what to clear:

| gate | required for v3.8.0 | as of Phase 16B |
|---|---|---|
| Docker tested end-to-end on at least one supported platform | yes | **NOT VERIFIED** in this session (Docker unavailable) |
| External user can reproduce latest proof from a fresh clone | yes | **PARTIAL** — local-clone passes; remote/fresh-clone against post-merge main pending P13.5 |
| README top section concise (≤ 10 lines summarising current state) | yes | **PASS** — Phase 15 / 16A / 16B refresh keeps it concise |
| No provider dependency in inner loop | yes | **PASS** (locked by tests) |
| 100+ auto-promoted solvers in proof | yes | **PASS** — Phase 16B P4 raised the canonical corpus to 104 |
| Security self-audit with documented findings | yes | **PARTIAL** — `pip-audit` and CI grep clean; `bandit` not installed |
| Stable release gate explicitly passed | yes | **NOT YET** — this document is the gate ledger; P11 fail-closed decision is `v3.7.6-stabilization-alpha` |

Until a separate session passes the stable gate, the **default tag for any Phase 16-ish work is prerelease only**.

## Tag versioning policy

| version range | what it means | gate |
|---|---|---|
| `v3.6.x` | substrate landed; no runtime behaviour change | Phase 10–11 substrate gate |
| `v3.7.x` | runtime-autonomy alpha increments | per-phase gate (Phase 11–16B+) |
| `v3.7.4-runtime-hint-alpha` (Phase 15) | automatic runtime hints + alpha release readiness | merged 2026-05-01 |
| `v3.7.5-upstream-runtime-alpha` (Phase 16A) | upstream `structured_request` propagation + restart continuity | merged 2026-05-01 |
| **`v3.7.6-stabilization-alpha`** (Phase 16B) | stabilization, full-corpus restart, proof soak, 100+ release gate, security self-audit, install/HTTP truth | this document |
| `v3.8.0` | first stable release | requires the stable gate above |
| `v4.0.0` | federation / multi-instance work | reserved; not before an explicit federation phase |

Any Phase 16B successor session producing more autonomy-lane improvements should use the next prerelease tag (e.g. `v3.7.7-...-alpha`) until the stable gate is explicitly passed.

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
