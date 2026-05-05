# Phase 18A — Host Export + Validation Verification

**Date (UTC):** 2026-05-05
**Branch:** `phase18a/benchmark-externalization-schema`
**Bundle path:** `docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle/`

## Commands

```
python tools/run_phase18a_benchmark_externalization.py \
    --out-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle \
    --validate

python tools/validate_phase18a_benchmark_bundle.py \
    --bundle-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle
```

## Result

```
Phase 18A - Benchmark Externalization Exporter
============================================================
Source root: <repo root>
Output dir : docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle
include_raw: False

Wrote bundle to docs/.../export_bundle
  artifacts : 3
  claims    : 16
  schemas   : 7
  reports   : 2
  release_gate_pass: True

Running validator...
Validator: PASS
```

Standalone validator:

```
Phase 18A bundle validation: PASS  (docs/.../export_bundle)
```

Both processes exited 0.

## Bundle facts

| field | value |
| --- | --- |
| `bundle_name` | `phase18a_benchmark_externalization` |
| `bundle_version` | `phase18a.v1` |
| `schema_version` | `benchmarks.v1` |
| `release_candidate` | `v3.10.0-benchmark-schema-alpha` |
| `artifact_count` | 3 (Phase 17B, 17C, 17D sanitized JSONs) |
| `claim_count` | 16 (PROVEN: 8, MEASURED: 1, MEASURED_LOCAL_OLLAMA_ONE_MODEL: 1, MEASURED_LOCAL_OLLAMA_PANEL: 1, NOT_CLAIMED: 2 — plus 3 honesty PROVEN) |
| `release_gate_pass` | `true` |
| `provider_jobs_delta` | 0 |
| `builder_jobs_delta` | 0 |
| `no_model_pull_or_download` | `true` |
| `no_cloud_api_calls` | `true` |
| `no_raw_intelligence_superiority_claim` | `true` |
| `no_cross_vendor_ranking_claim` | `true` |
| `no_consciousness_claim` | `true` |

## Required validation checks (all PASS)

1. All 8 required bundle files present (`benchmark_bundle_manifest.json`, `artifact_index.json`, `claim_evidence_ledger.json`, `release_lineage.json`, `checksums.sha256`, `README.md`, `reports/benchmark_bundle_index.md`, `reports/claim_evidence_ledger.md`).
2. All 7 schemas present and parse as valid JSON.
3. Each top-level doc validates against its schema.
4. `checksums.sha256` parses; every line resolves to a present file with matching SHA-256.
5. Each artifact validates against its declared schema (`local_efficiency.schema.json`, `local_ollama_baseline.schema.json`, `local_model_sweep.schema.json`).
6. Sanitization scrub: every per-prompt `stdout`/`stderr` field is either absent or a redaction stub `{"redacted": true, "sha256": "...", "length": N}`. No plaintext model output remains.
7. Each claim's `evidence_artifact` resolves under `artifacts/`.
8. Each claim's `label` is in the allowed enum (`PROVEN`, `MEASURED`, `INFERRED`, `NOT_CLAIMED`, `NOT_RUN`, `MEASURED_LOCAL_ONLY`, `MEASURED_LOCAL_OLLAMA_ONE_MODEL`, `MEASURED_LOCAL_OLLAMA_PANEL`).
9. Each claim's `evidence_field_pointer` (RFC 6901 JSON Pointer) resolves inside the named artifact.
10. Markdown reports contain no forbidden-vocabulary substring (14 honesty denylist + 8 ranking-guard substrings).
11. Release lineage `stable_latest = v3.8.0`, `isPrerelease = false`, `is_github_latest = true`, `target_sha = 824176eb...`.
12. Release lineage `prereleases[]` includes all 4 v3.9.x alpha tags with their canonical SHAs.
13. Manifest top-level honesty flags all `true`.
14. All 16 required claim_ids present.
15. `release_gate_pass = true`.

## What is in the bundle

```
docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle/
├── README.md
├── benchmark_bundle_manifest.json
├── artifact_index.json
├── claim_evidence_ledger.json
├── release_lineage.json
├── checksums.sha256
├── schemas/  (7 schema files)
├── artifacts/
│   ├── phase17b_local_efficiency_benchmark.sanitized.json
│   ├── phase17c_local_ollama_baseline.sanitized.json
│   └── phase17d_local_model_sweep.sanitized.json
└── reports/
    ├── benchmark_bundle_index.md
    └── claim_evidence_ledger.md
```

The bundle is self-contained. Anyone with this directory can verify manifest + checksums + claim ledger without network.

## What is NOT in the bundle

* No raw model stdout / stderr — sanitized by default. Operator can re-export with `--include-raw` for human inspection, but that disables `release_gate_pass`.
* No copies of the Phase 11–17A proof scripts — they live in `tools/` of the source repo. Reproduce commands in the claim ledger point at the originals.
* No GitHub release pages or release notes — those are externalized to `gh release view` output.
* No new measurement runs — Phase 18A re-exports existing committed evidence.

## Honesty contracts

* No model pull or download.
* No cloud API calls.
* No cross-vendor ranking.
* No raw-intelligence superiority claim.
* No consciousness / sentience / AGI claim.
* `provider_jobs_delta = builder_jobs_delta = 0` end-to-end.
