# Phase 18F — P6 Docker `--network none` Verification

**Date (UTC):** 2026-05-06
**Image:** `waggledance:phase18f`
**Base:** `Dockerfile` from `phase18f/incremental-gap-replay` branch (= origin/main Dockerfile, unmodified by Phase 18F).
**`.dockerignore` change:** one carve-out — `!tools/run_phase18f_incremental_gap_replay_proof.py`.
**Docker:** Docker 29.4.1.

## Build

```
docker build -t waggledance:phase18f -f Dockerfile .
→ DONE
```

## Run 1 — Phase 18F incremental replay proof

```
docker run --rm --network none waggledance:phase18f \
  python tools/run_phase18f_incremental_gap_replay_proof.py --out-dir /tmp/p18f_docker
```

| Counter | Value |
| --- | --- |
| seed_inserted_event_count | 32 |
| first_replay_new_event_count | 32 |
| first_replay_registered_solver_count | 6 |
| first_replay_families_covered | 6 |
| first_replay_dispatch_success_count | 18 |
| no_op_idempotency_pass | True |
| appended_events_inserted | 12 |
| third_replay_new_event_count | 12 |
| third_replay_registered_solver_count | 6 |
| third_replay_families_covered | 6 |
| third_replay_dispatch_success_count | 18 |
| total_registered_solver_count | 12 |
| type_confusion_rejection_count | 3 |
| malformed_event_rejection_count | 4 |
| forbidden_field_rejections | 1 |
| detector_bridge_pass | True |
| lock_result | LOCKED_NOT_RUN |
| concurrent_replay_safety_pass | True |
| forbidden_claims_absent | True |
| **release_gate_pass** | **True** |

Exit status 0.

## Run 2 — Phase 18E carry-forward

```
docker run --rm --network none waggledance:phase18f \
  python tools/run_phase18e_runtime_gap_replay_proof.py --out-dir /tmp/p18f_docker_e
```

`release_gate_pass = True`, deltas 0/0. Exit status 0.

## Run 3 — Phase 18C carry-forward

```
docker run --rm --network none waggledance:phase18f \
  python tools/run_phase18c_mined_solver_runtime_dispatch_proof.py --out-dir /tmp/p18f_docker_c
```

`release_gate_pass = True`, deltas 0/0. Exit status 0.

## Run 4 — Phase 18B carry-forward

```
docker run --rm --network none waggledance:phase18f \
  python tools/run_phase18b_gap_miner_feedback_proof.py --out-dir /tmp/p18f_docker_b
```

`release_gate_pass = True`, deltas 0/0. Exit status 0.

## Run 5 — Phase 18A bundle validator

```
docker run --rm --network none waggledance:phase18f \
  python tools/validate_phase18a_benchmark_bundle.py \
    --bundle-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle
→ Phase 18A bundle validation: PASS
```

Exit status 0.

## Verdict

Five `docker run --rm --network none` invocations all exit 0. No network, no Ollama, no cloud API call, no live builder execution. Phase 18F + Phase 18E + Phase 18C + Phase 18B proofs and Phase 18A validator all reproduce identically inside the offline Docker image built from this branch.
