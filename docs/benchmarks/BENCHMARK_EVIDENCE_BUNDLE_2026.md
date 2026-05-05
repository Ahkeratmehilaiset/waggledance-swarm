# Benchmark Evidence Bundle — 2026-Q2

**Status:** Phase 18A snapshot, derived from this session's reproducible artifact only.
**Date:** 2026-05-05
**Branch:** `phase18a/benchmark-externalization-schema`
**Anchor:** `v3.10.0-benchmark-schema-alpha` candidate (PRERELEASE only). v3.8.0 remains GitHub Latest.

This document publishes the location, layout, and reproduction commands of the canonical Phase 18A benchmark evidence bundle. The bundle itself is the source of truth; this document is a thin index.

## Where the bundle lives

```
docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle/
```

All paths in this document are relative to that directory.

## What's in the bundle

| file | purpose |
|---|---|
| `benchmark_bundle_manifest.json` | top-level manifest (bundle/schema versions, counts, honesty flags) |
| `artifact_index.json` | one entry per sanitized artifact (path, SHA-256, declared schema) |
| `claim_evidence_ledger.json` | claim-id → label + evidence pointer + reproduce command |
| `release_lineage.json` | stable Latest + 4 prior prereleases + candidate |
| `checksums.sha256` | SHA-256 of every other file in the bundle |
| `README.md` | quickstart for human readers |
| `schemas/*.schema.json` | 7 JSON Schema files |
| `artifacts/*.sanitized.json` | sanitized exports of Phase 17B / 17C / 17D JSONs |
| `reports/benchmark_bundle_index.md` | human-readable bundle index |
| `reports/claim_evidence_ledger.md` | human-readable claim ledger |

## Reproduce

```
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
git checkout v3.10.0-benchmark-schema-alpha    # or stay on main
pip install -r requirements-ci.txt              # stdlib-only validator; nothing extra needed for the bundle
python tools/run_phase18a_benchmark_externalization.py --validate
python tools/validate_phase18a_benchmark_bundle.py \
    --bundle-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle
```

Both processes exit `0` if and only if the bundle is valid. The validator runs against any bundle directory; you can point it at a freshly-exported one in `/tmp` or at the committed canonical bundle in `docs/runs/...`.

## What the bundle proves

This session's bundle records:

* 3 sanitized artifacts (Phase 17B local efficiency benchmark, Phase 17C single-model Ollama baseline, Phase 17D 4-model Ollama panel).
* 16 claims with explicit labels (PROVEN / MEASURED / MEASURED_LOCAL_OLLAMA_ONE_MODEL / MEASURED_LOCAL_OLLAMA_PANEL / NOT_CLAIMED).
* 7 JSON Schema files defining the contracts.
* SHA-256 checksums for every file.
* Release lineage hard-coded to `v3.8.0` GitHub Latest plus the four v3.9.x alpha prereleases.
* `release_gate_pass = true`; `provider_jobs_delta = builder_jobs_delta = 0`; `no_model_pull_or_download = true`; `no_cloud_api_calls = true`.

## What the bundle does NOT do

* Does NOT introduce new measurements. Phase 18A re-exports existing committed Phase 17B/17C/17D evidence.
* Does NOT include raw model stdout. Per-prompt `stdout`/`stderr` fields are replaced with redaction stubs `{"redacted": true, "sha256": "...", "length": N}`. `--include-raw` is opt-in and disables `release_gate_pass`.
* Does NOT make any cross-vendor ranking claim. Per-model numbers in the Phase 17D panel are reported in selection order, side-by-side; the validator rejects any rendered Markdown that imports ranking substrings (`is faster than`, `outperforms`, `beats`, `better than`, `ranks higher`, `ranked first`, `best of breed`, `is slower than`).
* Does NOT make any raw-intelligence superiority claim. The ledger surfaces this as an explicit `NOT_CLAIMED` entry.
* Does NOT execute Stage-2 atomic flip. Does NOT collect `HUMAN_APPROVAL.yaml`. Does NOT widen the six-family allowlist.
* Does NOT modify the v3.8.0, v3.9.0-producer-fabric-alpha, v3.9.1-local-efficiency-benchmark-alpha, v3.9.2-local-ollama-baseline-alpha, or v3.9.3-local-model-sweep-alpha tags.

## Validator behavior in one bullet

`tools/validate_phase18a_benchmark_bundle.py` is stdlib-only (no `jsonschema` dep) and rejects the bundle on any of: missing required file, schema violation, checksum mismatch, unknown label, unresolved evidence reference, raw stdout leakage, forbidden vocabulary substring in rendered MD, lineage drift away from `v3.8.0` Latest, or honesty-flag drift.

## Position in the 2026-Q2 release line

| Tag | What it adds | Status |
|---|---|---|
| `v3.8.0` | stable release | **Latest** |
| `v3.9.0-producer-fabric-alpha` | Phase 17A producer fabric + 10k scale | Pre-release |
| `v3.9.1-local-efficiency-benchmark-alpha` | Phase 17B local efficiency benchmark harness | Pre-release |
| `v3.9.2-local-ollama-baseline-alpha` | Phase 17C local Ollama baseline (Track F MEASURED, one model) | Pre-release |
| `v3.9.3-local-model-sweep-alpha` | Phase 17D local Ollama panel + repeatability | Pre-release |
| `v3.10.0-benchmark-schema-alpha` | Phase 18A bundle export + schema validation (this PR's candidate) | Pre-release (candidate) |

Phase 18A does not modify any earlier tag. v3.8.0 remains GitHub Latest.
