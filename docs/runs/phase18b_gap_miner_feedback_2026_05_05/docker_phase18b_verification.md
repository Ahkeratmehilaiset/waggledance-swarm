# Phase 18B — Docker `--network none` Verification

**Date (UTC):** 2026-05-05
**Branch:** `phase18b/gap-miner-feedback`
**Image:** `waggledance:phase18b`
**Image manifest list:** `sha256:ca36a1c752f8eabcad70ab61f216706e0083cd38aa4712bbea78e192e742ac6c`
**Image config:** `sha256:eae839770be86ccb6166ebb7eb2efa99eeeeb24b2b1d5b8e269ec8444160624b`

This document records the offline-by-policy proof for Phase 18B. The image carries the new gap-miner proof harness, the new mainline gap-mining modules, the carry-forward Phase 18A validator, and the canonical Phase 18A evidence bundle. The container has no Ollama, no network, no cloud reachability.

## Build

```
docker build -t waggledance:phase18b -f Dockerfile .
```

`.dockerignore` carve-outs added in this PR (on top of Phase 16F + 17A + 17B + 17C + 17D + 18A):

```
!tools/run_phase18b_gap_miner_feedback_proof.py
!docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle/
!docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle/**
```

The new gap-mining modules under `waggledance/core/autonomy_growth/{gap_mining,gap_candidate}.py` are already in the image because the `waggledance/` package directory was never in `.dockerignore`.

## Run 1 — Phase 18B gap-miner proof

```
docker run --rm --network none waggledance:phase18b \
    python tools/run_phase18b_gap_miner_feedback_proof.py \
        --out-dir /tmp/phase18b_docker
```

Result:

```
signals_total                : 30
candidates_total             : 14
allowlisted_candidates_total : 6
insufficient_evidence_total  : 3
out_of_family_rejected_total : 2
high_risk_rejected_total     : 1
builder_handoff_quarantined  : 1
duplicates_suppressed_total  : 1
solver_specs_total           : 6
provider/builder delta       : 0/0
forbidden_claims_absent      : True
release_gate_pass            : True
```

Exit 0.

## Run 2 — Phase 18A bundle validator (carry-forward)

```
docker run --rm --network none waggledance:phase18b \
    python tools/validate_phase18a_benchmark_bundle.py \
        --bundle-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle
```

Result:

```
Phase 18A bundle validation: PASS  (docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle)
```

Exit 0. The committed Phase 18A bundle (re-exported in Phase 18B P0 with LF-stable bytes) validates inside the container with the exact host SHA-256 checksums.

## What this proves

* The Phase 18B gap miner runs end-to-end with `--network none`. It is fixture-driven; no DB, no Ollama, no cloud reachability needed.
* All six Phase 18B verdicts fire correctly inside the container: 6 ALLOWLISTED, 3 INSUFFICIENT_EVIDENCE, 2 OUT_OF_FAMILY, 1 HIGH_RISK, 1 BUILDER_HANDOFF_QUARANTINED, 1 DUPLICATE_SUPPRESSED.
* The Phase 18A bundle's checksums verify inside the container. Phase 18B's P0 byte-stability fix (LF-only writes via `_write_text_lf`) makes the bundle reproducible across host and container.
* `provider_jobs_delta = builder_jobs_delta = 0` end-to-end. `no_model_pull_or_download = no_cloud_api_calls = true`. Six-family allowlist unchanged.

## Cross-check across the 2026-Q2 release line

| Tag/branch | Image | `--network none` runtime status |
|---|---|---|
| v3.7.8-docker-gate-alpha (Phase 16D) | `waggledance:phase16f` | PROVEN (Phase 16F) |
| v3.8.0 stable | `waggledance:v3.8.0-rc` | PROVEN (Phase 16F) |
| v3.9.0-producer-fabric-alpha | `waggledance:v3.9.0-producer-fabric-alpha-rc` | PROVEN (Phase 17A) |
| v3.9.1-local-efficiency-benchmark-alpha | `waggledance:v3.9.1-local-efficiency-benchmark-alpha-rc` | PROVEN (Phase 17B) |
| v3.9.2-local-ollama-baseline-alpha | `waggledance:phase17c` | PROVEN (Phase 17C) |
| v3.9.3-local-model-sweep-alpha | `waggledance:phase17d` | PROVEN (Phase 17D) |
| v3.10.0-benchmark-schema-alpha | `waggledance:phase18a` | PROVEN (Phase 18A) |
| v3.10.1-gap-miner-feedback-alpha (candidate) | **`waggledance:phase18b`** | **PROVEN (this Phase 18B run)** |

Phase 18B does not modify any earlier image or tag. v3.8.0 remains GitHub Latest.
