# Phase 18D — P5 Docker `--network none` Carry-Forward

**Date (UTC):** 2026-05-06
**Image:** `waggledance:phase18d`
**Base:** `Dockerfile` from `phase18d/local-delta-docs` branch (= `origin/main` Dockerfile, unmodified by Phase 18D).
**Docker:** Docker 29.4.1, build 055a478.

Phase 18D introduces no new proof harness, no new tool, and no `Dockerfile` / `.dockerignore` change. The Docker carry-forward verifies that the unchanged Phase 18A / 18B / 18C offline proofs still pass under `--network none` on a freshly built image from this branch.

## Build

```
docker build -t waggledance:phase18d -f Dockerfile .
→ DONE (manifest sha256:82272449ceedb8616a9b9af54f616ca37d0fb186bcc7662b8c2054d072834600)
```

## Run 1 — Phase 18A bundle validator

```
docker run --rm --network none waggledance:phase18d \
  python tools/validate_phase18a_benchmark_bundle.py \
    --bundle-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle
→ Phase 18A bundle validation: PASS
```

Exit status 0.

## Run 2 — Phase 18B gap-miner feedback proof

```
docker run --rm --network none waggledance:phase18d \
  python tools/run_phase18b_gap_miner_feedback_proof.py --out-dir /tmp/phase18d_docker_p18b
```

| Counter | Value |
| --- | --- |
| signals_total | 30 |
| candidates_total | 14 |
| allowlisted_candidates_total | 6 |
| insufficient_evidence_total | 3 |
| out_of_family_rejected_total | 2 |
| high_risk_rejected_total | 1 |
| builder_handoff_quarantined | 1 |
| duplicates_suppressed_total | 1 |
| solver_specs_total | 6 |
| provider_jobs_delta / builder_jobs_delta | 0 / 0 |
| forbidden_claims_absent | True |
| release_gate_pass | True |

Exit status 0.

## Run 3 — Phase 18C mined-solver runtime-dispatch proof

```
docker run --rm --network none waggledance:phase18d \
  python tools/run_phase18c_mined_solver_runtime_dispatch_proof.py --out-dir /tmp/phase18d_docker_p18c
```

| Counter | Value |
| --- | --- |
| signals_total | 30 |
| candidates_total | 14 |
| allowlisted_candidate_count | 6 |
| registered_solver_count | 6 |
| rejected_registration_count | 8 |
| dispatch_case_count | 18 |
| dispatch_success_count | 18 |
| dispatch_failure_count | 0 |
| families_covered | 6 |
| provider_jobs_delta / builder_jobs_delta | 0 / 0 |
| forbidden_claims_absent | True |
| release_gate_pass | True |

Exit status 0.

## Verdict

Three `docker run --rm --network none` invocations all exit 0 with `release_gate_pass = true`. No network, no Ollama pull, no cloud API call, no live builder execution. Phase 18A / 18B / 18C offline reproducibility is intact on this branch.
