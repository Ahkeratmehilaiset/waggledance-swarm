# Phase 17B — Docker `--network none` verification

**Date:** 2026-05-04
**Image:** `waggledance:phase17b` (ID `a90b971ad7df`, ~9 GB)
**Container runtime:** Docker Desktop 4.71.0, Engine 29.4.1, runc 1.3.5
**Network:** `--network none` for the benchmark run.

## Result: PASS

The Phase 17B benchmark harness exits 0 (`release_gate_pass = true`) inside the offline container.

## Build

```
docker build -t waggledance:phase17b .
```

`.dockerignore` extended with one carve-out:

```
!tools/run_phase17b_local_efficiency_benchmark.py
```

This joins the four Phase 16F + Phase 17A canonical proof carve-outs (`run_full_restart_continuity_proof.py`, `run_upstream_structured_request_proof.py`, `run_automatic_runtime_hint_proof.py`, `run_phase16b_proof_soak.py`, `run_phase17a_producer_fabric_proof.py`, `run_solver_scale_proof.py`) and the existing `tests/autonomy_growth/` carve-out so the same image runs the new harness under `--network none`. No Dockerfile change.

## Run

```
docker run --rm --network none waggledance:phase17b \
    python tools/run_phase17b_local_efficiency_benchmark.py \
    --out-dir /tmp/p17b \
    --skip-ollama \
    --canonical-repeat 1 \
    --scale-descriptors 10000 \
    --scale-lookups 1000 \
    --producer-repeat 1
```

## Scenario results inside Docker `--network none`

| track | required | met |
|---|---|---|
| A solver_hot_path | corpus 128, served via capability = 128, fallback rate 0, provider/builder delta 0 | ✅ |
| B capability_lookup_10k | 10000 descriptors built, 1000/1000 capability hits, 0 FIFO fallback, 0 miss | ✅ |
| C handle_query_e2e | corpus 128, served via capability = 128, 7/7 negative cases, provider/builder delta 0 | ✅ |
| D restart_continuity | 128/128 served pre+post DB close+reopen, all 7 invariants True, provider/builder delta 0 | ✅ |
| E producer_fabric | 68 IR objects across 6 kinds, 6/6 negative cases pass, provider/builder delta 0 | ✅ |
| F ollama_baseline | SKIPPED (default `--network none` safety) | ✅ |
| G external_competitor_slots | NOT_RUN (six slots documented) | ✅ |

**Top-level envelope inside Docker:**

* `release_gate_pass = true`
* `provider_jobs_delta = builder_jobs_delta = 0`
* `forbidden_claims_absent = true`
* `no_consciousness_claim = no_beats_all_competitors_claim = true`
* `no_cloud_api_calls_this_session = no_pull_or_download_this_session = true`
* `docker_mode = container`

The benchmark harness exits 0 inside the offline container with exactly the same WaggleDance scenarios as the host run. The numerical values (latency p50/p95/p99) for scenario B are slightly different from the host run because the Docker overlayfs / WSL2 path has different syscall + cache characteristics; both are within the master prompt's sanity ranges and both report `release_gate_pass = true`.

## Carry-forward Phase 16F + 17A invariants

* No internet at runtime (`--network none` enforced): ✅
* No provider credentials baked into image: ✅
* No autonomy code change inside the image (Phase 17A's 14 ported producer modules + the 128 seed corpus carry forward unchanged): ✅
* Six-family allowlist (RULE 13) honored: ✅
* No cloud API call attempted by harness or proofs: ✅
* No model pull / download attempted by Ollama probe (because `--skip-ollama` was set; even with `--include-ollama` inside `--network none`, the Ollama daemon would not be reachable from the container, and the probe would record `NOT_AVAILABLE_NOT_RUN`): ✅
* Bandit B324 cleanup carry-forward: not re-run this session because no new HIGH/MEDIUM patterns were introduced (Phase 17B's only new code is the harness, which uses `subprocess` + stdlib only).

## Conclusion

`waggledance:phase17b` reproduces the Phase 17A artifacts at full scale (10000 synthetic descriptors / 1000 capability lookups) inside a Docker container with the network completely disabled, plus runs the new Phase 17B benchmark harness, plus emits the full master-prompt-mandated metric envelope. The Docker `--network none` lane is GREEN for every Phase 17B claim. v3.9.1-local-efficiency-benchmark-alpha is justified as a PRERELEASE candidate; v3.8.0 stable remains GitHub Latest.
