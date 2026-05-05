# Phase 17D — Docker `--network none` Verification

**Date (UTC):** 2026-05-05
**Branch:** `phase17d/local-model-sweep`
**Image:** `waggledance:phase17d` (built from `Dockerfile` HEAD on this branch)
**Image manifest:** `sha256:2a7de27cf75fdd5b62b0e05134e04bc41c5e09b9c94875f762416bd9d440e1bf`
**Image manifest list:** `sha256:5335abf192e1574d24b34dfaf93c7325f2876fe795ed3962746e63a1ad2f8973`

This document records the offline-by-policy proof for Phase 17D's WaggleDance carry-forward path. The Phase 17D multi-model sweep itself targets the local Ollama daemon (which runs OUTSIDE the container by design); the Docker proof verifies that the WaggleDance proof scripts and the Phase 17C harness still run end-to-end with the network disabled.

## Build

```
docker build -t waggledance:phase17d -f Dockerfile .
```

`.dockerignore` carve-out for the new harness:

```
!tools/run_phase17d_local_model_sweep.py
```

(adjacent to existing carve-outs for `run_phase17c_local_ollama_baseline.py`, `run_phase17b_local_efficiency_benchmark.py`, etc.)

## Run

```
docker run --rm --network none waggledance:phase17d \
    python tools/run_phase17c_local_ollama_baseline.py \
        --skip-ollama --allow-no-ollama-track \
        --output /tmp/phase17d_docker.json
```

* `--network none`: no DNS, no API host reachable, no `host.docker.internal` route.
* `--skip-ollama`: forces the Ollama track to `NOT_AVAILABLE_NOT_RUN`.
* `--allow-no-ollama-track`: lets the harness exit 0 when the Ollama track is unavailable.

## Result

```
Phase 17C - Local Ollama Baseline Harness
============================================================
[17B] running aggregator (skip-ollama) -> /tmp/.../...
Wrote /tmp/phase17d_docker.json
Wrote /tmp/phase17d_docker.md

Phase 17B aggregator pass:   True
Ollama baseline status:      NOT_AVAILABLE_NOT_RUN
Selected ollama model:       None
Provider/builder delta:      0/0
Forbidden claims absent:     True
Release gate pass:           True
```

`exit code = 0`.

## Host carry-forward (same harness, same flags, on host)

```
python tools/run_phase17c_local_ollama_baseline.py \
    --skip-ollama --allow-no-ollama-track \
    --output /tmp/phase17d_wd_carry/phase17c_carry.json
```

Result identical:

```
Phase 17B aggregator pass:   True
Ollama baseline status:      NOT_AVAILABLE_NOT_RUN
Selected ollama model:       None
Provider/builder delta:      0/0
Forbidden claims absent:     True
Release gate pass:           True
```

Host and Docker results match — the Phase 17C carry-forward path is offline-safe in both environments.

## What this proves

* The Phase 17C harness (which exercises the Phase 11–17A WaggleDance proofs A–E plus the Ollama track in `NOT_AVAILABLE_NOT_RUN` mode) runs cleanly end-to-end with `--network none` on `waggledance:phase17d`.
* No cloud API was contacted — the network is disabled.
* No Ollama model was pulled or downloaded — the Ollama binary is not even present in the container.
* `provider_jobs_delta = builder_jobs_delta = 0` carry forward through the entire stack, identical to Phase 17C and Phase 17B carry-forwards.
* `forbidden_claims_absent = true` after rendering both the JSON and the MD.

## What this does NOT prove

* It does NOT prove the Phase 17D multi-model sweep itself runs in `--network none`. That is by design — the Ollama daemon runs OUTSIDE the container, so the multi-model panel is a host-only measurement. The canonical Phase 17D numbers live in `phase17d_local_model_sweep.{json,md}` (host).
* It does NOT exercise the new `tools/run_phase17d_local_model_sweep.py` inside the container. Phase 17D's containerized contract is: "the carry-forward path still works, and the new tool ships in the image (via the .dockerignore carve-out) for offline verification by future sessions."

## Cross-check across the 2026-Q2 release line

| Tag/branch | Image | `--network none` runtime status |
|---|---|---|
| v3.7.8-docker-gate-alpha (Phase 16D) | `waggledance:phase16f` | PROVEN (Phase 16F) |
| v3.8.0 stable | `waggledance:v3.8.0-rc` | PROVEN (Phase 16F) |
| v3.9.0-producer-fabric-alpha | `waggledance:v3.9.0-producer-fabric-alpha-rc` | PROVEN (Phase 17A) |
| v3.9.1-local-efficiency-benchmark-alpha | `waggledance:v3.9.1-local-efficiency-benchmark-alpha-rc` | PROVEN (Phase 17B) |
| v3.9.2-local-ollama-baseline-alpha | `waggledance:phase17c` | PROVEN (Phase 17C) |
| v3.9.3-local-model-sweep-alpha (candidate) | **`waggledance:phase17d`** | **PROVEN (this Phase 17D run)** |

Phase 17D does not modify any earlier image or tag. v3.8.0 remains GitHub Latest.
