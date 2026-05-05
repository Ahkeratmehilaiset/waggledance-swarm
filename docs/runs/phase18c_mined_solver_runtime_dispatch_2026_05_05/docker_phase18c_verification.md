# Phase 18C — Docker `--network none` Verification

**Date (UTC):** 2026-05-05
**Branch:** `phase18c/mined-solver-runtime-dispatch`
**Image:** `waggledance:phase18c`
**Image manifest list:** `sha256:34d146e09158a39bda102fe52e7f3f5c9aae9737979ef38d7de18a95fc4c765f`
**Image config:** `sha256:44d368d835fbe13f80aebb92713f1125804a08785563aa0bedc2a1bae1123ac7`

The image carries the new Phase 18C runtime registry + dispatch proof harness, the Phase 18B gap-miner harness, the Phase 18A validator + canonical bundle, and all WaggleDance autonomy modules. The container has no Ollama, no network, no cloud reachability.

## Build

```
docker build -t waggledance:phase18c -f Dockerfile .
```

`.dockerignore` carve-out added in this PR (on top of Phase 16F + 17A + 17B + 17C + 17D + 18A + 18B):

```
!tools/run_phase18c_mined_solver_runtime_dispatch_proof.py
```

The new mainline modules `waggledance/core/autonomy_growth/mined_solver_runtime.py` and the test file at `tests/autonomy_growth/test_phase18c_mined_solver_runtime_dispatch.py` are already in the image because their parent paths (`waggledance/`, `tests/autonomy_growth/`) were never excluded.

## Run 1 — Phase 18C proof harness

```
docker run --rm --network none waggledance:phase18c \
    python tools/run_phase18c_mined_solver_runtime_dispatch_proof.py \
        --out-dir /tmp/p18c_docker
```

Result:

```
signals_total                  : 30
candidates_total               : 14
allowlisted_candidate_count    : 6
registered_solver_count        : 6
rejected_registration_count    : 8
dispatch_case_count            : 18
dispatch_success_count         : 18
dispatch_failure_count         : 0
families_covered               : 6
provider/builder delta         : 0/0
forbidden_claims_absent        : True
release_gate_pass              : True
```

Exit 0.

## Run 2 — Phase 18B carry-forward

```
docker run --rm --network none waggledance:phase18c \
    python tools/run_phase18b_gap_miner_feedback_proof.py \
        --out-dir /tmp/p18b_carry_docker
```

Result:

```
provider/builder delta : 0/0
forbidden_claims_absent: True
release_gate_pass      : True
```

Exit 0.

## Run 3 — Phase 18A bundle validation carry-forward

```
docker run --rm --network none waggledance:phase18c \
    python tools/validate_phase18a_benchmark_bundle.py \
        --bundle-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle
```

Result:

```
Phase 18A bundle validation: PASS  (docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle)
```

Exit 0.

## What this proves

* The Phase 18C harness runs end-to-end with `--network none`. It uses an in-container `/tmp` SQLite ControlPlaneDB; no host DB file is created or committed.
* All 18 dispatch cases hit the registered mined solvers via the real `LowRiskSolverDispatcher.dispatch_by_features()` path inside the container — six families × three cases each.
* Phase 18B proof carries forward identically (30 signals, 14 candidates, six verdicts, `release_gate_pass=true`).
* Phase 18A bundle validates inside the container with byte-stable LF normalization (the exporter EOL fix from Phase 18B).
* `provider_jobs_delta = builder_jobs_delta = 0` end-to-end. `no_model_pull_or_download = no_cloud_api_calls = no_live_builder_execution = true`. Six-family allowlist unchanged.

## Cross-check across the 2026-Q2 release line

| Tag | Image | `--network none` runtime status |
|---|---|---|
| v3.7.8-docker-gate-alpha (Phase 16D) | `waggledance:phase16f` | PROVEN (Phase 16F) |
| v3.8.0 stable | `waggledance:v3.8.0-rc` | PROVEN (Phase 16F) |
| v3.9.0-producer-fabric-alpha | `waggledance:v3.9.0-producer-fabric-alpha-rc` | PROVEN (Phase 17A) |
| v3.9.1-local-efficiency-benchmark-alpha | `waggledance:v3.9.1-local-efficiency-benchmark-alpha-rc` | PROVEN (Phase 17B) |
| v3.9.2-local-ollama-baseline-alpha | `waggledance:phase17c` | PROVEN (Phase 17C) |
| v3.9.3-local-model-sweep-alpha | `waggledance:phase17d` | PROVEN (Phase 17D) |
| v3.10.0-benchmark-schema-alpha | `waggledance:phase18a` | PROVEN (Phase 18A) |
| v3.10.1-gap-miner-feedback-alpha | `waggledance:phase18b` | PROVEN (Phase 18B) |
| v3.10.2-mined-solver-dispatch-alpha (candidate) | **`waggledance:phase18c`** | **PROVEN (this Phase 18C run)** |

Phase 18C does not modify any earlier image or tag. v3.8.0 remains GitHub Latest.
