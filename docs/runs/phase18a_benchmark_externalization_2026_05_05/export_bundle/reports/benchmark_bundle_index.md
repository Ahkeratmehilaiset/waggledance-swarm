# Phase 18A Benchmark Bundle - Index

**Bundle name:** phase18a_benchmark_externalization
**Bundle version:** phase18a.v1
**Schema version:** benchmarks.v1
**Generated UTC:** 2026-05-05T06:56:47Z
**Git SHA:** b9dd6b7d9e55d3c9d1543a4a1409af0923ed34a8
**Source branch:** phase18a/benchmark-externalization-schema
**Release candidate:** v3.10.0-benchmark-schema-alpha

## Honesty declarations

* **No cloud API calls** were made.
* **No model was pulled or downloaded.**
* **No cross-vendor ranking is implied.**
* WaggleDance does not assert raw-intelligence superiority.

## Artifacts

| artifact_id | phase | declared_schema | exported_sha256 (head) |
| --- | --- | --- | --- |
| `phase17b_local_efficiency_benchmark` | `phase17b` | `local_efficiency.schema.json` | `f6290d5949b4dfc1...` |
| `phase17c_local_ollama_baseline` | `phase17c` | `local_ollama_baseline.schema.json` | `8b41e0617953a1c2...` |
| `phase17d_local_model_sweep` | `phase17d` | `local_model_sweep.schema.json` | `6872f553f3567b11...` |

## Schemas

* `schemas/benchmark_bundle.schema.json`
* `schemas/artifact_index.schema.json`
* `schemas/claim_evidence_ledger.schema.json`
* `schemas/release_lineage.schema.json`
* `schemas/local_efficiency.schema.json`
* `schemas/local_ollama_baseline.schema.json`
* `schemas/local_model_sweep.schema.json`

## Reports

* `reports/benchmark_bundle_index.md`
* `reports/claim_evidence_ledger.md`

## Reproduce

```
python tools/run_phase18a_benchmark_externalization.py --out-dir <dir> --validate
python tools/validate_phase18a_benchmark_bundle.py --bundle-dir <dir>
```

