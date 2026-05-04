# Phase 17C — Docker `--network none` Verification

**Date (UTC):** 2026-05-04
**Branch:** `phase17c/local-ollama-baseline`
**Image:** `waggledance:phase17c` (built from `Dockerfile` HEAD on this branch)
**Image manifest:** `sha256:5f7d37eb6218ccf3c8be46a87c4f6fd2c5385c25fbeab03d9114368f0adb3472`
**Image manifest list:** `sha256:5247bd3c770134c78dd116460c427f815944e8f2e5e81aa8e8e31a1af2cbd7a1`

This document records the offline-by-policy proof for Phase 17C: the new harness `tools/run_phase17c_local_ollama_baseline.py` runs end-to-end inside a Docker container with `--network none`, without any Ollama binary present, without any cloud API access, and without any model pull/download.

## Build

```
docker build -t waggledance:phase17c -f Dockerfile .
```

Builds on top of the same `python:3.13-slim` base used by the v3.8.0 stable gate. The `requirements-ci.txt` Python deps are CACHED from prior phase images. `.dockerignore` carve-out for the new harness:

```
!tools/run_phase17c_local_ollama_baseline.py
```

(adjacent to existing carve-outs for `run_phase17b_local_efficiency_benchmark.py`, `run_solver_scale_proof.py`, `run_phase17a_producer_fabric_proof.py`, etc.)

## Run

```
docker run --rm --network none waggledance:phase17c \
    python tools/run_phase17c_local_ollama_baseline.py \
        --skip-ollama --allow-no-ollama-track \
        --output /tmp/phase17c_docker.json
```

* `--network none`: no DNS, no API host reachable, no `host.docker.internal` route.
* `--skip-ollama`: forces the Ollama track to `NOT_AVAILABLE_NOT_RUN` even if the binary were present (it is not in this image).
* `--allow-no-ollama-track`: lets the harness exit 0 when the Ollama track is unavailable. The container is the documented use case for this flag.

## Result

```
Phase 17C - Local Ollama Baseline Harness
============================================================
[17B] running aggregator (skip-ollama) -> /tmp/.../...
Wrote /tmp/phase17c_docker.json
Wrote /tmp/phase17c_docker.md

Phase 17B aggregator pass:   True
Ollama baseline status:      NOT_AVAILABLE_NOT_RUN
Selected ollama model:       None
Provider/builder delta:      0/0
Forbidden claims absent:     True
Release gate pass:           True
```

`exit code = 0`.

## What this proves

* The Phase 17C harness, the Phase 17B aggregator, and all five Phase 11–17A canonical proof scripts (A_solver_hot_path, B_capability_lookup_10k, C_handle_query_e2e, D_restart_continuity, E_producer_fabric) run end-to-end with the network disabled.
* The Ollama track gracefully degrades to `NOT_AVAILABLE_NOT_RUN` when the binary is absent, without violating the release gate (under `--allow-no-ollama-track`).
* No cloud API was contacted (the network is disabled; no DNS lookups possible).
* No Ollama model was pulled or downloaded (the binary is not even present in the container).
* `provider_jobs_delta = builder_jobs_delta = 0` carry forward through the entire stack.
* `forbidden_claims_absent = true` after rendering both the JSON and the MD.

## What this does NOT prove

* It does NOT prove the Ollama track itself runs in `--network none`. By design, the Ollama daemon runs outside the container (the v3.8.0 image notes `OLLAMA_HOST=http://host.docker.internal:11434`); the host run in `phase17c_local_ollama_baseline.{json,md}` is the canonical record for the MEASURED Ollama probe.
* It does NOT re-derive the WaggleDance track numbers — it only proves the offline code path. The canonical numbers for the Phase 17C session are the host run.

## Cross-check against earlier phases

| Tag/branch | Image | `--network none` runtime status |
|---|---|---|
| v3.7.8-docker-gate-alpha (Phase 16D) | `waggledance:phase16f` | PROVEN (Phase 16F) |
| v3.8.0 stable | `waggledance:v3.8.0-rc` | PROVEN (Phase 16F) |
| v3.9.0-producer-fabric-alpha | `waggledance:v3.9.0-producer-fabric-alpha-rc` | PROVEN (Phase 17A) |
| v3.9.1-local-efficiency-benchmark-alpha | `waggledance:v3.9.1-local-efficiency-benchmark-alpha-rc` | PROVEN (Phase 17B) |
| v3.9.2-local-ollama-baseline-alpha (candidate) | **`waggledance:phase17c`** | **PROVEN (this Phase 17C run)** |

Phase 17C does not modify any earlier image or tag. v3.8.0 remains GitHub Latest.
