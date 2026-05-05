# Benchmark Artifact Schema — `benchmarks.v1`

**Status:** Phase 18A introduces this schema family. Every artifact in the Phase 18A evidence bundle conforms to one of the schemas under `schemas/benchmarks/v1/`.

**Anchor:** `v3.10.0-benchmark-schema-alpha` candidate (PRERELEASE only). v3.8.0 remains GitHub Latest.

This document explains the schema family. The authoritative contract is the JSON Schema files themselves; this document is a thin reader's index.

## File layout

```
schemas/benchmarks/v1/
  benchmark_bundle.schema.json         # top-level bundle manifest
  artifact_index.schema.json           # per-artifact index entry shape
  claim_evidence_ledger.schema.json    # claim-id → label + evidence pointer
  release_lineage.schema.json          # stable Latest + prereleases
  local_efficiency.schema.json         # Phase 17B aggregator JSON shape
  local_ollama_baseline.schema.json    # Phase 17C single-model JSON shape
  local_model_sweep.schema.json        # Phase 17D panel JSON shape
```

Each schema declares `$schema = https://json-schema.org/draft/2020-12/schema`, a `$id` namespaced under this repo, a `title`, and a `required[]` list. The validator under `tools/validate_phase18a_benchmark_bundle.py` implements just enough of Draft 2020-12 (`type`, `required`, `enum`, `pattern`, `properties`, `items`, `additionalProperties: false`, `minimum`, `minItems`) to enforce these contracts — no `jsonschema` pip dependency.

## Bundle manifest (`benchmark_bundle.schema.json`)

The single entry point. Top-level required keys:

* `bundle_name` — `"phase18a_benchmark_externalization"`.
* `bundle_version` — `"phase18a.v1"`.
* `schema_version` — `"benchmarks.v1"`.
* `generated_at_utc` — ISO 8601 UTC timestamp.
* `git_sha` — full hex git SHA the bundle was generated from.
* `source_branch` — branch name.
* `release_candidate` — `"v3.10.0-benchmark-schema-alpha"`.
* `artifact_count`, `claim_count` — non-negative integers.
* `checksums_file` — `"checksums.sha256"`.
* `release_gate_pass` — must be `true` for a sanitized bundle.
* `provider_jobs_delta`, `builder_jobs_delta` — must both be `0`.
* `no_model_pull_or_download`, `no_cloud_api_calls`, `no_raw_intelligence_superiority_claim`, `no_cross_vendor_ranking_claim`, `no_consciousness_claim` — must all be `true`.
* `schemas_listed[]`, `artifacts_listed[]`, `reports_listed[]` — file lists relative to bundle root.

`additionalProperties: false` is enforced — unknown keys reject the bundle.

## Artifact index (`artifact_index.schema.json`)

One entry per sanitized benchmark artifact:

* `artifact_id` — stable identifier (e.g., `phase17c_local_ollama_baseline`).
* `phase` — `"phase17b"`, `"phase17c"`, or `"phase17d"`.
* `path_in_bundle` — relative path under `artifacts/`.
* `source_path_in_repo` — original committed location.
* `source_sha256` — SHA-256 of the source bytes (64 hex).
* `exported_sha256` — SHA-256 of the sanitized export.
* `declared_schema` — schema filename the artifact validates against.
* `raw_fields_redacted` — `true` for sanitized exports, `false` for `--include-raw` exports.

## Claim evidence ledger (`claim_evidence_ledger.schema.json`)

The machine-readable mapping from claim to evidence. Required keys per entry:

* `claim_id` — stable, kebab-or-snake case (e.g., `local_ollama_panel_measured`).
* `label` — one of:
  * `PROVEN`
  * `MEASURED`
  * `INFERRED`
  * `NOT_CLAIMED`
  * `NOT_RUN`
  * `MEASURED_LOCAL_ONLY`
  * `MEASURED_LOCAL_OLLAMA_ONE_MODEL`
  * `MEASURED_LOCAL_OLLAMA_PANEL`
* `title` — short human-readable claim title.
* `evidence_artifact` — sanitized artifact filename.
* `evidence_path_in_bundle` — full relative path.
* `source_path_in_repo`, `source_sha256` — committed source provenance.
* `evidence_field_pointer` — RFC 6901 JSON Pointer into the artifact (e.g., `/release_gate_pass`, `/panel_summary/models_measured_count`).
* `evidence_value_type` — declared scalar type at that pointer.
* `caveat` — engineering disclaimer in plain prose.
* `reproduce_command` — the exact command a future session runs to regenerate the evidence.
* `scope` — what the claim is bounded to.
* `not_claimed[]` — the explicit non-claims this entry surfaces.

The validator asserts every label is in the allowed enum, every `evidence_path_in_bundle` resolves to a present file, every JSON Pointer resolves inside that file, and the 16 canonical `claim_id`s are all present.

## Release lineage (`release_lineage.schema.json`)

* `stable_latest` — must be `{"tag": "v3.8.0", "target_sha": "824176eb...", "isPrerelease": false, "is_github_latest": true, "publishedAt": "..."}`.
* `prereleases[]` — must include `v3.9.0-producer-fabric-alpha`, `v3.9.1-local-efficiency-benchmark-alpha`, `v3.9.2-local-ollama-baseline-alpha`, `v3.9.3-local-model-sweep-alpha` with their canonical target SHAs.
* `candidate` — `{"tag": "v3.10.0-benchmark-schema-alpha", "expected_isPrerelease": true, "expected_is_github_latest": false}`.

If any of these drift, the validator rejects the bundle.

## Per-artifact schemas

* **`local_efficiency.schema.json`** — Phase 17B aggregator JSON. Required: `phase = "phase17b_local_efficiency_benchmark"`, `benchmark_version = "phase17b.v1"`, `tracks`, `scenarios`, `claim_labels`, `provider_jobs_delta = 0`, `builder_jobs_delta = 0`, `release_gate_pass = true`, all 5 honesty booleans = `true`.
* **`local_ollama_baseline.schema.json`** — Phase 17C single-model JSON. Required: `benchmark_version = "phase17c.v1"`, `ollama_baseline_status ∈ {MEASURED, NOT_AVAILABLE_NOT_RUN, FAILED}`, `ollama_track` object with `prompt_count`/`prompts_succeeded`/`prompts_failed`, `provider_jobs_delta = 0`, `builder_jobs_delta = 0`, `release_gate_pass = true`, `forbidden_claims_absent = true`, `no_model_pull_or_download = true`, `no_cloud_api_calls = true`.
* **`local_model_sweep.schema.json`** — Phase 17D panel JSON. Required: `benchmark_version = "phase17d.v1"`, `selected_models[]`, `model_results` object, `panel_summary` object, `repeat_count`, `prompt_count`, plus the same provider/builder/release-gate/honesty constraints as Phase 17C.

## Sanitization

Sanitized exports rewrite per-prompt `stdout` and `stderr` fields to:

```json
{"redacted": true, "sha256": "<sha256-of-original-bytes>", "length": <int>}
```

The validator's leakage scrub asserts that no `stdout` or `stderr` field anywhere in any artifact contains a plaintext string or an object that fails the redaction-stub shape. `--include-raw` is opt-in and disables `release_gate_pass`.

## Forbidden vocabulary

The validator scans `README.md`, `reports/benchmark_bundle_index.md`, and `reports/claim_evidence_ledger.md` for these substrings (case-insensitive); a hit rejects the bundle:

```
conscious, sentient, aware, alive, agi, revolutionary, magical,
human-like mind, self-aware, explosive intelligence, emergent,
beats all competitors, world's best, world's fastest,
is faster than, is slower than, outperforms,
" beats ", ranks higher, ranked first, best of breed, better than
```

Compound technical terms (`capability-aware`, `context-aware`, `self-model`) and explicit non-claim phrasings (`does not claim to be aware`, etc.) are whitelisted via a strip pass before scanning.

## How to extend

To add a new claim:

1. Append a new entry to `_build_claim_ledger()` in `tools/run_phase18a_benchmark_externalization.py`.
2. Set `label` to one of the allowed enum values.
3. Set `evidence_artifact`, `evidence_path_in_bundle`, `source_path_in_repo`, `source_sha256`, `evidence_field_pointer` so the validator can resolve them.
4. Add the new `claim_id` to `REQUIRED_CLAIM_IDS` in `tools/validate_phase18a_benchmark_bundle.py` if it must be present in every bundle.
5. Re-run the exporter and validator. CI must stay green.

To add a new artifact:

1. Append to `SOURCE_ARTIFACTS` in the exporter.
2. Author or extend a per-artifact schema under `schemas/benchmarks/v1/`.
3. Add tests in `tests/benchmarks/test_phase18a_benchmark_externalization.py`.
