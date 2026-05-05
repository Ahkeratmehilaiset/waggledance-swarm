# Phase 18A Benchmark Evidence Bundle

This directory is the canonical Phase 18A benchmark evidence bundle for the
`v3.10.0-benchmark-schema-alpha` candidate prerelease.

* `benchmark_bundle_manifest.json` - top-level manifest.
* `artifact_index.json` - one entry per sanitized benchmark artifact.
* `claim_evidence_ledger.json` - claim-id -> label + evidence pointer.
* `release_lineage.json` - stable Latest + four prior prereleases.
* `checksums.sha256` - SHA-256 of every file in the bundle.
* `schemas/` - JSON Schemas for the manifest, ledger, lineage, and the
  three sanitized artifacts.
* `artifacts/` - sanitized exports of Phase 17B / 17C / 17D JSONs.
* `reports/` - human-readable Markdown index + claim ledger.

## Honesty declarations

* No cloud API calls were made.
* No Ollama model was pulled or downloaded.
* No cross-vendor ranking is implied.
* WaggleDance does not assert raw-intelligence superiority.

## Validate this bundle

```
python tools/validate_phase18a_benchmark_bundle.py --bundle-dir <this-directory>
```

Exits 0 only if every check passes.
