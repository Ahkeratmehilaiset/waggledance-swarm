# Phase 18A Benchmark Bundle - Index

**Bundle name:** phase18a_benchmark_externalization
**Bundle version:** phase18a.v1
**Schema version:** benchmarks.v1
**Generated UTC:** 2026-05-05T13:54:19Z
**Git SHA:** 2d32b9b2267d271508d689f94f4631e2965f3be2
**Source branch:** phase18b/gap-miner-feedback
**Release candidate:** v3.10.0-benchmark-schema-alpha

## Honesty declarations

* **No cloud API calls** were made.
* **No model was pulled or downloaded.**
* **No cross-vendor ranking is implied.**
* WaggleDance does not assert raw-intelligence superiority.

## Artifacts

| artifact_id | phase | declared_schema | exported_sha256 (head) |
| --- | --- | --- | --- |
| `phase17b_local_efficiency_benchmark` | `phase17b` | `local_efficiency.schema.json` | `981e2505ba5afef6...` |
| `phase17c_local_ollama_baseline` | `phase17c` | `local_ollama_baseline.schema.json` | `e17a6200c631ce64...` |
| `phase17d_local_model_sweep` | `phase17d` | `local_model_sweep.schema.json` | `6b1b7c6e8b36a4ab...` |

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

