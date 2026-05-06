# Phase 18E — P6 Docker `--network none` Verification

**Date (UTC):** 2026-05-06
**Image:** `waggledance:phase18e`
**Base:** `Dockerfile` from `phase18e/runtime-gap-replay` branch (= `origin/main` Dockerfile, unmodified by Phase 18E).
**`.dockerignore` change:** one carve-out added — `!tools/run_phase18e_runtime_gap_replay_proof.py`. No other change.
**Docker:** Docker 29.4.1.

## Build

```
docker build -t waggledance:phase18e -f Dockerfile .
→ DONE (manifest sha256:8a5b53b602da9b5fa34367b2bd99b29e76e36efe6b730aa99c3bfc91e00f2148)
```

## Run 1 — Phase 18E persisted-replay proof

```
docker run --rm --network none waggledance:phase18e \
  python tools/run_phase18e_runtime_gap_replay_proof.py --out-dir /tmp/p18e_docker
```

| Counter | Value |
| --- | --- |
| persisted_event_count | 32 |
| loaded_event_count | 32 |
| malformed_event_rejection_count | 3 |
| forbidden_field_rejections | 1 |
| allowlisted_candidate_count | 6 |
| registered_solver_count | 6 |
| non_allowlisted_rejected_count | 7 |
| dispatch_case_count | 18 |
| dispatch_success_count | 18 |
| dispatch_failure_count | 0 |
| families_covered | 6 |
| replay_idempotency_pass | True |
| provider_jobs_delta / builder_jobs_delta | 0 / 0 |
| forbidden_claims_absent | True |
| **release_gate_pass** | **True** |

Exit status 0.

## Run 2 — Phase 18C carry-forward (mined-solver runtime dispatch proof)

```
docker run --rm --network none waggledance:phase18e \
  python tools/run_phase18c_mined_solver_runtime_dispatch_proof.py --out-dir /tmp/p18e_docker_c
```

`release_gate_pass = True`, dispatch_failure_count 0, families_covered 6, deltas 0/0. Exit status 0.

## Run 3 — Phase 18B carry-forward (gap-miner feedback proof)

```
docker run --rm --network none waggledance:phase18e \
  python tools/run_phase18b_gap_miner_feedback_proof.py --out-dir /tmp/p18e_docker_b
```

`release_gate_pass = True`, 6 solver_specs, 1 duplicates_suppressed, deltas 0/0. Exit status 0.

## Run 4 — Phase 18A bundle validator

```
docker run --rm --network none waggledance:phase18e \
  python tools/validate_phase18a_benchmark_bundle.py \
    --bundle-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle
→ Phase 18A bundle validation: PASS
```

Exit status 0.

## Verdict

Four `docker run --rm --network none` invocations all exit 0 with `release_gate_pass = true` (or PASS for the validator). No network, no Ollama, no cloud API call, no live builder execution. The Phase 18E persisted-replay proof, Phase 18C runtime dispatch proof, Phase 18B gap-miner proof, and Phase 18A bundle validator all reproduce identically inside the `--network none` Docker image built from this branch.
