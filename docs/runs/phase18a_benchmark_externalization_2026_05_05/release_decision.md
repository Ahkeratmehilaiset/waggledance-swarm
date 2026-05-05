# Phase 18A — Release Decision

**Decision:** **A — release `v3.10.0-benchmark-schema-alpha` PRERELEASE.**
**Date (UTC):** 2026-05-05
**Branch:** `phase18a/benchmark-externalization-schema`
**Base SHA:** `b9dd6b7d9e55d3c9d1543a4a1409af0923ed34a8` (Phase 17D post-release docs PR #78 merge)

## Gate evaluation

All Phase 18A release gates green:

| Gate | Result |
| --- | --- |
| P0 baseline verified | yes |
| 7 schemas written | yes (`schemas/benchmarks/v1/*.schema.json`) |
| Exporter written | yes (`tools/run_phase18a_benchmark_externalization.py`, ~470 LOC) |
| Validator written | yes (`tools/validate_phase18a_benchmark_bundle.py`, ~330 LOC, stdlib-only) |
| Export bundle generated | 3 artifacts, 16 claims, 7 schemas, 2 reports |
| Validator accepts bundle | PASS (host + Docker) |
| Sanitized export default works | yes; `--include-raw` opt-in disables `release_gate_pass` |
| Claim ledger validates | 16 claims, all labels in allowed enum, all evidence pointers resolve |
| Checksums verify | every line in `checksums.sha256` resolves to a present file with matching SHA-256 |
| No raw stdout leakage | scrub PASS; `stdout`/`stderr` fields are absent or redaction stubs |
| No unsupported claims | 22-substring forbidden-vocabulary scrub PASS on all 3 rendered MD files |
| Targeted tests | **179 / 179 PASS** (Phase 18A 15 + Phase 17D 13 + Phase 17C 15 + phase10 14 + storage 50 + ui_hologram 22 + solver_router 50) |
| Docker `--network none` | PASS (`waggledance:phase18a`, combined export + validate, exit 0) |
| `git rev-parse v3.8.0^{}` | `824176ebf2a6b8debed41982090a125cbe2ddad1` (unchanged) |
| `git rev-parse v3.9.0-producer-fabric-alpha^{}` | `c726995c816ee4c09e031c2190c3de6592e82879` (unchanged) |
| `git rev-parse v3.9.1-local-efficiency-benchmark-alpha^{}` | `f4d0a4a4152ca74e98a8d7f7161c233075bf4111` (unchanged) |
| `git rev-parse v3.9.2-local-ollama-baseline-alpha^{}` | `db5d7db1ecb9ae6f17293f0bf7261f4c9d40e91c` (unchanged) |
| `git rev-parse v3.9.3-local-model-sweep-alpha^{}` | `d0704efe46be18d480ed425ff83b087cd36ef9bd` (unchanged) |
| `gh release list` | v3.8.0 still **Latest** |

## Bundle metrics

* `bundle_name = phase18a_benchmark_externalization`
* `bundle_version = phase18a.v1`
* `schema_version = benchmarks.v1`
* `release_candidate = v3.10.0-benchmark-schema-alpha`
* `artifact_count = 3` (Phase 17B + 17C + 17D sanitized JSONs)
* `claim_count = 16`
* `release_gate_pass = true`
* `provider_jobs_delta = builder_jobs_delta = 0`
* All boolean honesty flags in the manifest = `true`.

## Tag plan

* Tag name: `v3.10.0-benchmark-schema-alpha`.
* `isPrerelease = true`. **NOT** `Latest`.
* Target: the squash-merge commit of the Phase 18A PR.
* GitHub release: created via `gh release create v3.10.0-benchmark-schema-alpha --prerelease --target <merge SHA>`.
* `v3.8.0` remains GitHub Latest. v3.9.0 + v3.9.1 + v3.9.2 + v3.9.3 + v3.10.0 alphas all Pre-release.

## What this release does NOT do

* Does NOT modify the v3.8.0, v3.9.0-producer-fabric-alpha, v3.9.1-local-efficiency-benchmark-alpha, v3.9.2-local-ollama-baseline-alpha, or v3.9.3-local-model-sweep-alpha tags.
* Does NOT introduce a stable-tagged release.
* Does NOT add new measurements — Phase 18A re-exports existing committed Phase 17B/17C/17D evidence.
* Does NOT pull or download any Ollama model.
* Does NOT call any cloud LLM API.
* Does NOT widen the six-family allowlist.
* Does NOT execute Stage-2 atomic flip; does NOT collect HUMAN_APPROVAL.
* Does NOT make any cross-vendor ranking claim or raw-intelligence superiority claim.
* Does NOT add any new pip dependency.
