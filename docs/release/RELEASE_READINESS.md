# Release Readiness — WaggleDance (v3.8.0 stable + v3.9.0/v3.9.1/v3.9.2/v3.9.3 + v3.10.0 + v3.10.1 PRERELEASE + v3.10.2-mined-solver-dispatch-alpha CANDIDATE)

**Stable status:** **v3.8.0 stable released 2026-05-04** from Phase 16F PR #68 (squash-merge `824176eb`). All 22 stable gates final = PASS. **GitHub Latest = v3.8.0**.

**Released prerelease (Phase 17A):** **v3.9.0-producer-fabric-alpha released 2026-05-04T18:32:47Z** from PR #71 (squash-merge `c726995c`) on top of v3.8.0. Pre-release entry on GitHub; v3.8.0 remains GitHub Latest, untouched, not retagged.

**Released prerelease (Phase 17B):** **v3.9.1-local-efficiency-benchmark-alpha** (released 2026-05-04T20:59:09Z from PR #73 squash-merge `f4d0a4a4`). Pre-release entry on GitHub. Phase 17B is a benchmark-infrastructure prerelease — it adds `tools/run_phase17b_local_efficiency_benchmark.py` and the matching tests/docs but **does not** add a raw-intelligence model, a new autonomy mechanism, an allowlist family, a Stage-2 flip, a HUMAN_APPROVAL artifact, an actuator autonomy lane, or any new provider HTTP route. Measured-evidence prerelease, not a feature prerelease.

**Released prerelease (Phase 17C):** **v3.9.2-local-ollama-baseline-alpha** (released 2026-05-04T22:26:28Z from PR #75 squash-merge `db5d7db1`). Pre-release entry on GitHub. Phase 17C upgrades the Phase 17B optional Ollama track from `SKIPPED_OPTIONAL` to `MEASURED` for one already-installed local model (`gemma4:e4b`). Host run: `release_gate_pass = true`, 30/30 prompts succeed, median latency 0.7866 s, p95 17.5538 s.

**Released prerelease (Phase 17D):** **v3.9.3-local-model-sweep-alpha** (released 2026-05-05T06:05:30Z from PR #77 squash-merge `d0704efe`). Pre-release entry on GitHub. 4-model Ollama panel × 3 repeats × 30 prompts = 360/360 ok; CoV 0.002–0.029. Honesty: no model pull, no cloud API call, no allowlist widening, no Stage-2 flip, no cross-vendor ranking implied.

**Released prerelease (Phase 18A):** **v3.10.0-benchmark-schema-alpha** (released 2026-05-05T07:20:31Z from PR #79 squash-merge `4554b24a`). Pre-release entry on GitHub. Phase 18A externalizes Phase 17B / 17C / 17D benchmark artifacts as a versioned, validated, offline-exportable evidence bundle with 7 JSON Schemas (Draft 2020-12), a 16-claim machine-readable ledger, SHA-256 checksums, and a stdlib-only validator.

**Released prerelease (Phase 18B):** **v3.10.1-gap-miner-feedback-alpha** (released 2026-05-05T14:32:46Z from PR #81 squash-merge `b408b14a`). Pre-release entry on GitHub. Closed the runtime-gap-mining feedback half of the autonomous learning loop with a six-element verdict enum over structured runtime gap signals.

**Candidate prerelease (Phase 18C):** **v3.10.2-mined-solver-dispatch-alpha** — to be tagged from the Phase 18C PR squash-merge SHA after PR-level CI green and `--match-head-commit`-protected merge. Phase 18C closes the explicit Phase 18B gap (`capability_lookup_status = NOT_RUN_OUT_OF_PHASE18B_SCOPE`) by registering Phase 18B mined ALLOWLISTED low-risk solver specs into the **real** `ControlPlaneDB` via the canonical Phase 17A four-step pattern and dispatching them through the **real** `LowRiskSolverDispatcher.dispatch_by_features()`. New mainline module `waggledance/core/autonomy_growth/mined_solver_runtime.py` (~310 LOC, stdlib + WaggleDance only, no new pip dependency) with `compile_mined_spec_to_runtime_artifact`, `register_mined_solver_specs`, `RegistrationSummary`. Per-family compilation table covers exactly the six Phase 18B fixture shapes; novel signatures fail closed. New proof harness `tools/run_phase18c_mined_solver_runtime_dispatch_proof.py`; 33 unit tests. Host run: 6 ALLOWLISTED candidates → 6 registered auto-promoted solvers; 18/18 deterministic dispatch cases hit (3 per family × 6 families) via capability-aware path; 8 non-allowlisted verdicts rejected from registration; `release_gate_pass = true`, all gates green. Docker `--network none` PASS for Phase 18C proof + Phase 18B carry-forward + Phase 18A bundle validation. Builder handoff remains quarantined; zero solver rows for builder-handoff candidates. Honesty: no model pull/download, no cloud API call, no live builder execution, no allowlist widening, no autonomy code change outside Phase 18C's module, no Stage-2 flip, no HUMAN_APPROVAL collected, no consciousness claim, no "beats all competitors" claim, no cross-vendor ranking, no raw-intelligence superiority claim, no new pip dependency, no DB/SQLite files committed, no token/secret exposure.

This document defines what `release-ready` means in the WaggleDance autonomy lineage, what version-tag policy applies, and which gates must pass before each release tier is created.

`release-ready` here means **truthful readiness**: reproducible proofs both locally and inside Docker `--network none`, green CI, clear docs, security audit pass-through, known limitations documented, no consciousness claim.

## Latest tag (GitHub Latest) — v3.8.0 (Phase 16F, stable, released 2026-05-04)

`v3.8.0` — first stable release. Phase 16F closed the single remaining v3.8.0 stable blocker from Phase 16D (g01 Docker end-to-end + g19 Docker runtime no-network) by building `waggledance:phase16f` (`python:3.13-slim` + `requirements-ci.txt`, 3.09 GB) and running all four canonical proofs inside Docker `--network none` at corpus 104, exactly matching local results. PR #68 squash-merged at 2026-05-04T07:08:23Z (merge commit `824176eb`); annotated tag `v3.8.0` pushed; GitHub release published with `isPrerelease=false` at 2026-05-04T07:13:27Z. All 22 stable gates final = PASS.

## Latest prerelease — v3.9.0-producer-fabric-alpha (Phase 17A)

`v3.9.0-producer-fabric-alpha` — Phase 17A producer fabric and 10k solver capability scale prerelease on top of v3.8.0 stable. PR #71 squash-merged at 2026-05-04T18:28:06Z (merge commit `c726995c`); annotated tag pushed; GitHub release published with `isPrerelease=true` at 2026-05-04T18:32:47Z.

What this prerelease ships beyond v3.8.0:

* **14 phase8.5 producer modules ported** (~4,511 LOC stdlib + waggledance only): `waggledance/core/dreaming/*` (curriculum, collapse, meta_proposal, replay, request_pack, shadow_graph), `waggledance/core/magma/{self_model, reflective_workspace}.py`, `waggledance/core/meta/*` (meta_learner, history, inputs, review_bundle).
* **`tools/run_phase17a_producer_fabric_proof.py`** — emits 68 IR objects across 6 kinds (curiosity, self_model, dream_curriculum, dream_meta_proposal, hive_proposals, review_bundle) consumed by the existing main IR adapters; 6/6 negative cases pass; provider/builder delta = 0; 18/18 integration tests.
* **`tools/run_solver_scale_proof.py`** — 10000 synthetic deterministic solver descriptors balanced across 6 families × 8 hex cells; 1000/1000 capability hits via the real `RuntimeQueryRouter.route()` capability-aware path; 0 FIFO fallback; 0 miss; lookup p50/p95/p99 inside Docker `--network none` ≈ 0.47/0.94/1.17 ms; 21/21 integration tests. Honesty label: `is_synthetic_scale=true, not_canonical_corpus=true`.
* **Canonical seed corpus 104 → 128** (+24 entries; +4 per family; no allowlist widening; per-family floors 32/21/21/18/18/18).
* **Phase 8.5 branch preservation on origin** — all 5 `phase8.5/*` branches now visible on origin via 4 fast-forward pushes (no force-push).
* **Honest competitor evidence docs** (`docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md` + `LOCAL_AI_RUNTIME_COMPARISON.md`) with PROVEN / MEASURED / INFERRED / NOT CLAIMED labels per axis. Raw intelligence vs frontier MoE: NOT CLAIMED.

What v3.9.0-producer-fabric-alpha is **not**: a stable release. It does not promote v3.9.0 to stable; that requires a separate stable-gate session and a paired benchmark against a frontier model that has not been run. v3.8.0 remains the recommended stable.

## Previous prerelease — v3.7.8-docker-gate-alpha (Phase 16D)

Phase 16D prerelease tag on `origin/main` at merge commit `7210a7e` (PR #66). All 16 Bandit B324 weak-hash findings resolved via `usedforsecurity=False`; persisted semantic fingerprint preservation verified per RULE 25; all four canonical proofs re-run at corpus 104 with `provider_jobs_delta = builder_jobs_delta = 0`; 3-iter soak 9/9 no flakes. Docker was not available in the Phase 16D dev shell. The Phase 16F session restored Docker availability and closed the gate.

The v3.7.8-docker-gate-alpha tag remains visible on GitHub as a Pre-release entry. **Not moved, not retagged, not amended** per master prompt rule 25.

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
* Phase 14 PR #61 last green. Phase 15 PR #62 green at merge commit `2b9978d`. Phase 16A PR #63 green at merge commit `d18e1da`. Phase 16B PR #64 green at merge commit `bada64c`. Phase 16C PR #65 green at merge commit `b87fbe4`. Phase 16D PR re-runs them on its head SHA.

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

## Release blockers — for stable v3.8.0 (still blocked, narrowest set ever after Phase 16D)

The strict fail-closed rule for stable v3.8.0 requires every required-for-stable gate to PASS on the post-merge `main` SHA. Phase 16D reduces the blocker set from Phase 16C's "1 substantive (Docker) + 1 residual cleanup (Bandit)" to **"1 substantive (Docker)" only**.

1. **g01 Docker end-to-end** — `docker` CLI is not installed in the Phase 16D development shell either. Same situation as Phase 16B and Phase 16C. `Dockerfile` and `docker-compose.yml` inspected and unchanged. **Sole remaining substantive blocker for v3.8.0 stable.** An external operator with Docker installed must run `docker build -t waggledance:phase16d .`, then in-container proofs, and amend `RELEASE_READINESS.md` before v3.8.0 stable is justified. See `docs/runs/phase16d_final_stable_gate_closure_2026_05_02/docker_verification.md` for the exact verification path.

**Resolved in Phase 16D:**

* g05 Bandit residual cleanup is **fully closed**. All 16 B324 weak-hash findings resolved via `usedforsecurity=False`. Bandit HIGH count: **16 → 0**. Persisted semantic fingerprint preservation verified per RULE 25.
* g21 persisted semantic fingerprint preservation across the B324 cleanup: PASS (14 scalar fields + 7 restart invariants + 6 per-operation served counts identical pre and post).

These blockers are explicit by design; Phase 16D's outcome is the prerelease `v3.7.8-docker-gate-alpha`, a **success** outcome under stabilization-mode rules.

## Stable v3.8.0 — checklist as of Phase 16D

These are tracked here so the next release session knows what to clear:

| gate | required for v3.8.0 | as of Phase 16D |
|---|---|---|
| Docker tested end-to-end on at least one supported platform | yes | **NOT VERIFIED** in any session yet (Docker unavailable in Phase 16B, 16C, and 16D dev shells) |
| External user can reproduce latest proof from a fresh clone | yes | **PASS** — Phase 16B P13.5 PASSED vs `bada64c`; Phase 16C P10 PASSED vs `b87fbe4`; Phase 16D P10 will re-verify against post-Phase-16D-merge SHA |
| README top section concise (≤ 10 lines summarising current state) | yes | **PASS** — Phase 15 / 16A / 16B / 16C / 16D refresh keeps it concise |
| No provider dependency in inner loop | yes | **PASS** (locked by tests + four Phase 16D re-runs) |
| 100+ auto-promoted solvers in proof | yes | **PASS** — corpus 104 (since Phase 16B); Phase 16D re-confirms `auto_promotions_total = 104` |
| Security self-audit with documented findings | yes | **PASS** — Phase 16D: Bandit HIGH count 16 → 0; persisted semantic fingerprint preservation verified per RULE 25; `pip-audit` consistent with Phase 16C; CI grep clean |
| Stable release gate explicitly passed | yes | **NOT YET** — this document is the gate ledger; P8 fail-closed decision is `v3.7.8-docker-gate-alpha` (Docker still blocks) |

Until a separate session passes the stable gate, the **default tag for any Phase 16-ish work is prerelease only**.

## Tag versioning policy

| version range | what it means | gate |
|---|---|---|
| `v3.6.x` | substrate landed; no runtime behaviour change | Phase 10–11 substrate gate |
| `v3.7.x` | runtime-autonomy alpha increments | per-phase gate (Phase 11–16D+) |
| `v3.7.4-runtime-hint-alpha` (Phase 15) | automatic runtime hints + alpha release readiness | merged 2026-05-01 |
| `v3.7.5-upstream-runtime-alpha` (Phase 16A) | upstream `structured_request` propagation + restart continuity | merged 2026-05-01 |
| `v3.7.6-stabilization-alpha` (Phase 16B) | stabilization, full-corpus restart, proof soak, 100+ release gate, security self-audit, install/HTTP truth | merged 2026-05-01 |
| `v3.7.7-stable-gate-alpha` (Phase 16C) | stable-gate closure attempt: Bandit installed and run; full proof re-verification at corpus 104; Docker still unavailable | merged 2026-05-02 |
| **`v3.7.8-docker-gate-alpha`** (Phase 16D) | final stable-gate closure: 16 Bandit B324 weak-hash lints resolved with persisted semantics preserved; Docker still unavailable in dev shell | this document |
| `v3.8.0` | first stable release | requires Docker end-to-end verification only |
| `v4.0.0` | federation / multi-instance work | reserved; not before an explicit federation phase |

Any Phase 16D successor session producing more autonomy-lane improvements should use the next prerelease tag (e.g. `v3.7.9-...-alpha`) until the stable gate is explicitly passed. **At this point, only Docker stands between v3.7.8 and v3.8.0.**

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
