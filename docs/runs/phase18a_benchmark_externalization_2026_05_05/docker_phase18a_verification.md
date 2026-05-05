# Phase 18A — Docker `--network none` Verification

**Date (UTC):** 2026-05-05
**Branch:** `phase18a/benchmark-externalization-schema`
**Image:** `waggledance:phase18a` (built from `Dockerfile` HEAD on this branch)
**Image manifest list:** `sha256:e3f89f6d0bf4a05b742928c6d39b4a01773fd8d5bb675c49a45578e6b5c9971e`
**Image config:** `sha256:a3d16038b7b6ef19ce8e15a27b8ba6c7a696853ba9682e7d8a1df9bbedb3a5d4`

This document records the offline-by-policy proof that Phase 18A's exporter and validator run end-to-end inside Docker with the network disabled. Phase 18A's contract is a pure file-shuffle + checksum + validation exercise — no Ollama, no cloud LLM, no DNS, no model files needed at runtime.

## Build

```
docker build -t waggledance:phase18a -f Dockerfile .
```

`.dockerignore` carve-outs added in this PR:

```
!tools/run_phase18a_benchmark_externalization.py
!tools/validate_phase18a_benchmark_bundle.py
!schemas/
!tests/benchmarks/
!docs/runs/phase17b_local_efficiency_benchmark_2026_05_04/phase17b_local_efficiency_benchmark.json
!docs/runs/phase17c_local_ollama_baseline_2026_05_04/phase17c_local_ollama_baseline.json
!docs/runs/phase17d_local_model_sweep_2026_05_05/phase17d_local_model_sweep.json
```

The three `phase17*.json` carve-outs are required because the exporter ingests them as source artifacts. The `.md` siblings remain excluded (they are not used at export time). The 7 schemas under `schemas/benchmarks/v1/` are needed both for `--validate` and for the bundle to copy them into its own `schemas/` subdirectory.

## Run (combined export + validate, single shell command)

```
docker run --rm --network none waggledance:phase18a sh -lc \
  'python tools/run_phase18a_benchmark_externalization.py --out-dir /tmp/phase18a_export_bundle --validate \
   && python tools/validate_phase18a_benchmark_bundle.py --bundle-dir /tmp/phase18a_export_bundle'
```

* `--network none`: no DNS, no API host reachable, no `host.docker.internal` route.
* `sh -lc '... && ...'`: ensures both processes share the same `/tmp` so the standalone validator can read the bundle the exporter just wrote (separate `docker run` invocations would not share `/tmp`).
* `--validate`: invokes the validator immediately after the exporter writes the bundle.
* The standalone validator after `&&` is the second pass — same bundle, fresh process — proving the bundle is portable across processes inside the container.

## Result

```
Phase 18A - Benchmark Externalization Exporter
============================================================
Source root: /app
Output dir : /tmp/phase18a_export_bundle
include_raw: False

Wrote bundle to /tmp/phase18a_export_bundle
  artifacts : 3
  claims    : 16
  schemas   : 7
  reports   : 2
  release_gate_pass: True

Running validator...
Validator: PASS
Phase 18A bundle validation: PASS  (/tmp/phase18a_export_bundle)
```

`exit code = 0`.

## What this proves

* The Phase 18A exporter runs end-to-end in `--network none` — no cloud reachability needed.
* The Phase 18A validator runs end-to-end in `--network none` — schemas resolve from `schemas/` inside the container; checksums verify locally; JSON Pointers resolve inside the bundle.
* Sanitization is correct in the container: 3 sanitized artifacts, 16 claims, 7 schemas, 2 reports, manifest+index+ledger+lineage+checksums.
* `release_gate_pass = true` end-to-end. `provider_jobs_delta = builder_jobs_delta = 0`. `forbidden_claims_absent = true`.
* No cloud API was contacted (the network is disabled).
* No Ollama model was pulled or downloaded (Ollama isn't even installed in the container; Phase 18A doesn't need it).
* The carve-out for the three Phase 17B/17C/17D source JSONs is sufficient — every other Phase 18A dependency is the schemas/ tree, the two Python tools, and stdlib.

## What this does NOT prove

* It does NOT prove the Phase 17B/17C/17D measurement *runs* work in `--network none`. Those are proven separately:
  * Phase 17B: `docs/runs/phase17b_local_efficiency_benchmark_2026_05_04/docker_phase17b_verification.md`
  * Phase 17C: `docs/runs/phase17c_local_ollama_baseline_2026_05_04/docker_phase17c_verification.md`
  * Phase 17D: `docs/runs/phase17d_local_model_sweep_2026_05_05/docker_phase17d_verification.md`
* Phase 18A *re-exports* what those phases produced; it does not re-measure.

## Cross-check across the 2026-Q2 release line

| Tag/branch | Image | `--network none` runtime status |
|---|---|---|
| v3.7.8-docker-gate-alpha (Phase 16D) | `waggledance:phase16f` | PROVEN (Phase 16F) |
| v3.8.0 stable | `waggledance:v3.8.0-rc` | PROVEN (Phase 16F) |
| v3.9.0-producer-fabric-alpha | `waggledance:v3.9.0-producer-fabric-alpha-rc` | PROVEN (Phase 17A) |
| v3.9.1-local-efficiency-benchmark-alpha | `waggledance:v3.9.1-local-efficiency-benchmark-alpha-rc` | PROVEN (Phase 17B) |
| v3.9.2-local-ollama-baseline-alpha | `waggledance:phase17c` | PROVEN (Phase 17C) |
| v3.9.3-local-model-sweep-alpha | `waggledance:phase17d` | PROVEN (Phase 17D) |
| v3.10.0-benchmark-schema-alpha (candidate) | **`waggledance:phase18a`** | **PROVEN (this Phase 18A run)** |

Phase 18A does not modify any earlier image or tag. v3.8.0 remains GitHub Latest.
