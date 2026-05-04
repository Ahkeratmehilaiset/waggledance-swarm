# Phase 16F — Local critical proof rerun

**Date:** 2026-05-04
**Worktree:** `C:/Python/project2-phase16f-docker-stable-gate` @ `phase16f/docker-stable-gate` ahead of origin/main by 0 substantive code commits (only `.dockerignore`, `Dockerfile`, `requirements.lock.txt` Phase 16F build adjustments + Phase 16F session artifacts)
**Output dir:** `docs/runs/phase16f_docker_stable_gate_2026_05_03/`

## Result: PASS — all 4 local proofs match Docker results 1-to-1

| proof | corpus | served via capability | rejected | errored | provider Δ | builder Δ |
|---|---:|---:|---:|---:|---:|---:|
| Phase 15 hint | 104 | 104 | 0 | 0 | 0 | 0 |
| Phase 16A upstream | 104 | 104 | 0 | 0 | 0 | 0 |
| Phase 16B full restart | 104 | 104 (pre + post restart) | 0 | 0 | 0 | 0 |

## Proof artefacts written (committed as JSON / MD only — no .db files)

| file | purpose |
|---|---|
| `automatic_runtime_hint_proof.json` | Phase 15 hint proof JSON |
| `upstream_structured_request_proof.json` | Phase 16A upstream proof JSON |
| `upstream_structured_request_proof.md` | Phase 16A upstream proof human-readable summary |
| `full_restart_continuity_proof.json` | Phase 16B P2 full-restart proof JSON |
| `full_restart_continuity_proof.md` | Phase 16B P2 human-readable summary |
| `proof_soak_report.json` | Phase 16B P3 3-iter soak report |
| `automatic_runtime_hint_proof.db` | (NOT committed — listed for transparency) |
| `upstream_structured_request_proof.db` | (NOT committed) |
| `full_restart_continuity_proof.db` | (NOT committed) |

`.gitignore` and the master prompt's P4 rule both require `.db` files NOT to be staged. The pre-commit verification in P9 will assert this.

## Proof soak — `tools/run_phase16b_proof_soak.py --iterations 3`

```
phase15_runtime_hint    pass=3/3   elapsed_mean=37.58s   flake=False
phase16a_upstream       pass=3/3   elapsed_mean=40.66s   flake=False
phase16b_full_restart   pass=3/3   elapsed_mean=37.36s   flake=False
overall_pass            = True
overall_flake_detected  = False
total_iterations        = 9
soak_root               = (system temp dir; ephemeral)
```

**Verdict:** g08 (Proof soak 3 iter) PASS. 9/9 iterations, no flakes, mean ~38 s/iter.

## Latency comparison: Docker vs local

Docker latency was uniformly ~5-10 ms higher than local at p50; both well within Phase 14 budgets:

| stage | Docker p50/p99 | local p50/p99 |
|---|---|---|
| Phase 16A pass1 service.handle_query | 19.87 / 540.80 ms | 11.51 / 411.05 ms |
| Phase 16A pass2 cold | 16.04 / 29.88 ms | 10.19 / 23.96 ms |
| Phase 16A pass3 warm | 10.63 / 21.60 ms | 10.66 / 22.21 ms |
| Phase 16A upstream extractor only | 0.006 / 0.031 ms | 0.008 / 0.042 ms |
| Phase 15 pass2 cold handle_query | 17.02 / 28.38 ms | 9.93 / 18.43 ms |
| Phase 15 pass3 warm handle_query | 10.92 / 25.93 ms | 9.27 / 16.53 ms |
| Phase 15 hint extractor only | 0.015 / 0.067 ms | 0.017 / 0.058 ms |

Docker overhead is the WSL2 / overlayfs syscall-and-copy tax — expected, not a stable blocker. Hot-path counters (warm_hits=318, cold_hits_warmed=98, misses=104) are identical Docker vs local, confirming HotPathCache behaviour is ABI-stable across the runtime boundary.

## Stable gate ledger updates

* **g04 100+ solver release gate**: PASS (corpus 104 carried forward, asserted by `tests/autonomy_growth/test_seed_library.py::test_seed_library_meets_v3_8_0_release_gate_minimum` in P3 smoke + here)
* **g06 Provider/builder delta = 0**: PASS (0/0 in all 4 local proof runs)
* **g07 Full-corpus restart proof**: PASS (104/104 served pre+post, all restart invariants True)
* **g08 Proof soak 3 iter**: PASS (9/9, no flakes)

## Carry-forward identity with Phase 16D baseline

The four numeric / boolean outputs of Phase 16F local proofs match Phase 16D `docs/runs/phase16d_final_stable_gate_closure_2026_05_02/baseline_post_b324/` field-for-field:

* `corpus_total = 104`
* `auto_promotions_total = 104`
* `served_via_capability_lookup_total = 104`
* `provider_jobs_delta = builder_jobs_delta = 0`
* `growth_events_total = 416` (4 events per seed × 104)
* every restart invariant True
* `solver_capability_features_total = 180` (Phase 16B raised from 130)

Per RULE 25, the persisted SQLite DB byte hash legitimately differs run-to-run (page layout); semantic fingerprint preservation is what matters and is preserved.
