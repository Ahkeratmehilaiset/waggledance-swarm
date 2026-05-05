# Phase 18A — Benchmark Externalization + Schema Hardening (Design)

**Status:** Authoritative for Phase 18A. Code in `tools/run_phase18a_benchmark_externalization.py`, `tools/validate_phase18a_benchmark_bundle.py`, `schemas/benchmarks/v1/*.schema.json`, and `tests/benchmarks/test_phase18a_benchmark_externalization.py` MUST conform to this document.

This document exists per master-prompt rule "Do not code until this exists."

---

## 1. Purpose

Convert Phase 17B / 17C / 17D benchmark JSON artifacts from "repo-internal reports + ad-hoc JSON outputs" into a versioned, validated, offline-exportable evidence bundle with:

* a strict JSON Schema contract per artifact;
* a machine-readable manifest pointing at every artifact;
* a machine-readable claim ledger mapping each claim to evidence + source path + SHA-256 + reproduce command + label;
* a release lineage map enumerating v3.8.0 stable + four v3.9.x prereleases;
* SHA-256 checksums for every exported file;
* sanitized artifacts (raw model stdout fields replaced by redaction stubs);
* generated Markdown reports for human consumption.

Phase 18A does NOT perform new measurements. It packages existing evidence behind a hardened contract.

## 2. Scope and invariants

### In scope

1. Importing Phase 17B / 17C / 17D committed JSONs.
2. Sanitization of raw model stdout (`per_prompt[].stdout` etc. → `{"redacted": true, "sha256": "...", "length": N}`).
3. Schema authoring under `schemas/benchmarks/v1/`.
4. stdlib-only validator (no `jsonschema` dep, no other new pip deps).
5. CLI exporter with `--validate`, `--include-raw`, `--strict`, `--out-dir`, `--source-root`.
6. Tests for validator + exporter, including adversarial fixtures.
7. Docker `--network none` export + validate proof.
8. Docs + claim ledger + competitive evidence matrix axis upgrade.

### Out of scope (hard fail-closed)

1. Adding new measurement runs by default. (Validator is allowed to verify against committed artifacts.)
2. Pulling, downloading, or modifying any Ollama model.
3. Cloud LLM API calls.
4. Changing autonomy code, allowlist, canonical corpus, or any prior benchmark numbers.
5. Stable-tagged release.
6. Stage-2 atomic flip; HUMAN_APPROVAL collection.
7. Cross-vendor ranking. Raw-intelligence superiority claim.
8. Forbidden vocabulary substrings outside JSON disclaimer fields.
9. Editing `CURRENT_STATE.md` manually (master prompt rule 23 — note: distinct from `CURRENT_STATUS.md`).
10. Adding new pip dependencies.

## 3. Bundle layout

```
docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle/
  README.md
  benchmark_bundle_manifest.json
  artifact_index.json
  claim_evidence_ledger.json
  release_lineage.json
  checksums.sha256
  schemas/
    benchmark_bundle.schema.json
    artifact_index.schema.json
    claim_evidence_ledger.schema.json
    release_lineage.schema.json
    local_efficiency.schema.json
    local_ollama_baseline.schema.json
    local_model_sweep.schema.json
  artifacts/
    phase17b_local_efficiency_benchmark.sanitized.json
    phase17c_local_ollama_baseline.sanitized.json
    phase17d_local_model_sweep.sanitized.json
  reports/
    benchmark_bundle_index.md
    claim_evidence_ledger.md
```

Bundle is a self-contained directory. Anyone who clones the repo can `cd export_bundle/` and verify manifest + checksums + claim ledger without network.

## 4. Sanitization policy

By default the exporter rewrites raw model stdout fields. Phase 17C/17D `per_prompt[].stdout` and `per_prompt[].stderr` (when present) are replaced with:

```json
{"redacted": true, "sha256": "<sha256-of-original-bytes>", "length": <int>}
```

The original `stdout_hash` field (if it exists) is preserved unchanged. The original `latency_ms`, `exit_code`, `prompt_hash`, `family`, `index`, `timed_out` fields are all preserved unchanged.

`--include-raw` flag (default OFF) preserves raw stdout/stderr. The release gate uses sanitized export only. If a future operator wants to publish raw outputs they must opt in explicitly.

If a sanitized artifact is found to contain any byte-string field with the substring `stdout` or `stderr` whose value is not a redaction stub, the validator rejects the bundle.

## 5. Schema policy

Seven schema files under `schemas/benchmarks/v1/`. Each:

* Is valid JSON.
* Uses `$schema = "https://json-schema.org/draft/2020-12/schema"` for external readability.
* Carries a `$id` that namespaces it under `https://github.com/Ahkeratmehilaiset/waggledance-swarm/schemas/benchmarks/v1/<name>.schema.json` (URI is documentary only; no fetch happens).
* Declares `title`, `type`, `required`, and `properties`.
* Declares `enum` constraints for claim labels and a `pattern` for SHA-256 fields.

The validator implements a stdlib-only subset:

* `type` (string, integer, number, boolean, object, array, null)
* `required[]`
* `enum[]`
* `pattern` (Python `re`)
* `properties` (recursive)
* `items` (single schema, recursive)
* `additionalProperties: false` honored where set
* `$ref` to other schema files in the same directory (file-relative)
* `oneOf` — minimal support for the redaction stub vs raw bytes union

No external network fetch for any `$schema` or `$id`. No `jsonschema` library import. The validator implements just enough of Draft 2020-12 to enforce the contracts this bundle uses.

## 6. Claim labels (allowed enum)

```
PROVEN
MEASURED
INFERRED
NOT_CLAIMED
NOT_RUN
MEASURED_LOCAL_ONLY
MEASURED_LOCAL_OLLAMA_ONE_MODEL
MEASURED_LOCAL_OLLAMA_PANEL
```

Each ledger entry has shape:

```json
{
  "claim_id": "deterministic_routing_solver_first",
  "label": "PROVEN",
  "title": "Solver-first deterministic routing",
  "evidence_artifact": "phase17b_local_efficiency_benchmark.sanitized.json",
  "evidence_path_in_bundle": "artifacts/phase17b_local_efficiency_benchmark.sanitized.json",
  "source_path_in_repo": "docs/runs/phase17b_local_efficiency_benchmark_2026_05_04/phase17b_local_efficiency_benchmark.json",
  "source_sha256": "...",
  "evidence_field_pointer": "/tracks/A_solver_hot_path/raw/served_via_capability_lookup_total",
  "evidence_value_type": "integer",
  "caveat": "Measured this session; 128 corpus, 24-CPU Windows host.",
  "reproduce_command": "python tools/run_phase17b_local_efficiency_benchmark.py --skip-ollama",
  "scope": "WaggleDance autonomy hot-path; six-family low-risk allowlist.",
  "not_claimed": ["raw_intelligence_superiority", "cross_vendor_ranking"]
}
```

## 7. Required claims in the ledger

| claim_id | label | evidence source |
|---|---|---|
| `docker_offline_proven` | PROVEN | Phase 17B/17C/17D `docker_phase*_verification.md` |
| `producer_fabric_proven` | PROVEN | Phase 17B Track E + Phase 17A producer-fabric proof |
| `capability_lookup_10k_measured` | MEASURED | Phase 17B Track B raw |
| `canonical_corpus_128_proven` | PROVEN | Phase 17B Tracks A/C/D raw |
| `local_efficiency_harness_proven` | PROVEN | Phase 17B aggregator JSON |
| `local_ollama_one_model_measured` | MEASURED_LOCAL_OLLAMA_ONE_MODEL | Phase 17C |
| `local_ollama_panel_measured` | MEASURED_LOCAL_OLLAMA_PANEL | Phase 17D |
| `raw_intelligence_vs_frontier_moe_not_claimed` | NOT_CLAIMED | n/a (negative claim) |
| `cross_vendor_ranking_not_claimed` | NOT_CLAIMED | n/a (negative claim) |
| `no_model_pull_or_download` | PROVEN | Phase 17C/17D top-level invariants |
| `no_cloud_api_calls` | PROVEN | Phase 17C/17D top-level invariants |
| `provider_builder_delta_zero` | PROVEN | Phase 17B/17C/17D top-level invariants |
| `no_stage2_flip` | PROVEN | session_state.json invariants + run docs |
| `no_human_approval_collected` | PROVEN | session_state.json invariants + run docs |
| `no_allowlist_widening` | PROVEN | session_state.json invariants + run docs |
| `benchmark_artifact_externalization` | PROVEN | this bundle |

The validator asserts every entry above is present with a label in the allowed enum.

## 8. Release lineage schema

```json
{
  "stable_latest": {
    "tag": "v3.8.0",
    "target_sha": "824176ebf2a6b8debed41982090a125cbe2ddad1",
    "isPrerelease": false,
    "is_github_latest": true
  },
  "prereleases": [
    {"tag": "v3.9.0-producer-fabric-alpha", "target_sha": "c726995c..."},
    {"tag": "v3.9.1-local-efficiency-benchmark-alpha", "target_sha": "f4d0a4a4..."},
    {"tag": "v3.9.2-local-ollama-baseline-alpha", "target_sha": "db5d7db1..."},
    {"tag": "v3.9.3-local-model-sweep-alpha", "target_sha": "d0704efe..."}
  ],
  "candidate": {
    "tag": "v3.10.0-benchmark-schema-alpha",
    "expected_isPrerelease": true,
    "expected_is_github_latest": false
  }
}
```

The validator asserts `stable_latest.tag == "v3.8.0"` AND `stable_latest.isPrerelease == false` AND `stable_latest.is_github_latest == true`. If any of those drift the bundle is rejected.

## 9. Manifest schema (top-level)

```json
{
  "bundle_name": "phase18a_benchmark_externalization",
  "bundle_version": "phase18a.v1",
  "schema_version": "benchmarks.v1",
  "generated_at_utc": "<iso8601>",
  "git_sha": "<full hex>",
  "source_branch": "phase18a/benchmark-externalization-schema",
  "release_candidate": "v3.10.0-benchmark-schema-alpha",
  "artifact_count": <int>,
  "claim_count": <int>,
  "checksums_file": "checksums.sha256",
  "release_gate_pass": true,
  "provider_jobs_delta": 0,
  "builder_jobs_delta": 0,
  "no_model_pull_or_download": true,
  "no_cloud_api_calls": true,
  "no_raw_intelligence_superiority_claim": true,
  "no_cross_vendor_ranking_claim": true,
  "no_consciousness_claim": true,
  "schemas_listed": [...],
  "artifacts_listed": [...],
  "reports_listed": [...]
}
```

The validator asserts:

* `bundle_version == "phase18a.v1"`
* `schema_version == "benchmarks.v1"`
* `release_candidate == "v3.10.0-benchmark-schema-alpha"`
* `provider_jobs_delta == 0` AND `builder_jobs_delta == 0`
* All boolean honesty flags == `true`
* `artifact_count == len(artifacts_listed)`
* `claim_count` equals the number of entries in `claim_evidence_ledger.json`

## 10. Forbidden-substring scrub on rendered Markdown

The validator scans `reports/benchmark_bundle_index.md` and `reports/claim_evidence_ledger.md` for these substrings (case-insensitive). A hit rejects the bundle:

```
conscious, sentient, aware, alive, agi, revolutionary, magical,
human-like mind, self-aware, explosive intelligence, emergent,
beats all competitors, world's best, world's fastest,
is faster than, is slower than, outperforms, " beats ",
ranks higher, ranked first, best of breed, better than
```

(Same denylist as Phase 17C/17D, plus the 8 ranking-guard substrings from Phase 17D.)

The disclaimer fields in the manifest (`no_consciousness_claim` etc.) use compounded tokens that don't trip the substring scan.

## 11. Validator CLI

```
python tools/validate_phase18a_benchmark_bundle.py --bundle-dir <path>
```

Required validations (each rejects non-zero exit if it fails):

1. Manifest, artifact index, claim ledger, release lineage all exist and parse.
2. All 7 schema files exist and parse.
3. Manifest's `artifacts_listed[]` resolve under `artifacts/`.
4. Each artifact validates against its declared schema.
5. `checksums.sha256` exists and every line resolves to a present file with matching SHA-256.
6. Each claim's `evidence_artifact` resolves under `artifacts/`.
7. Each claim's `label` is in the allowed enum.
8. Each claim's `evidence_field_pointer` (RFC 6901 JSON Pointer) resolves inside the named artifact.
9. Sanitized artifacts contain no plaintext model output (every `stdout`/`stderr` field is either absent or a redaction stub).
10. Markdown reports contain no forbidden-vocabulary substring.
11. Release lineage `stable_latest` is `v3.8.0`, isPrerelease=false, is_github_latest=true.
12. Release lineage prereleases include all four v3.9.x alpha tags with their canonical SHAs.
13. Manifest top-level boolean honesty flags all true.
14. Required claims (per §7) all present.
15. `release_gate_pass == true`.

Exit code:

* `0` only if all validations pass.
* Non-zero on any violation, with a printed list of violations.

## 12. Exporter CLI

```
python tools/run_phase18a_benchmark_externalization.py \
    [--out-dir <path>] \
    [--source-root <path>] \
    [--validate] \
    [--include-raw] \
    [--strict]
```

* `--out-dir` — bundle output directory. Defaults to `docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle`.
* `--source-root` — repo root (defaults to script's parent's parent).
* `--validate` — invoke the validator after writing the bundle. Exit non-zero if validation fails.
* `--include-raw` — keep raw stdout/stderr in sanitized artifacts (default OFF).
* `--strict` — fail closed on any unexpected source-artifact field (default ON).

Default behavior:

1. Read each source JSON.
2. Compute SHA-256 of source bytes.
3. Sanitize: rewrite per-prompt stdout/stderr fields.
4. Write sanitized JSON to `artifacts/`.
5. Write 7 schema files to `schemas/`.
6. Write manifest, artifact index, claim ledger, release lineage to bundle root.
7. Write `checksums.sha256` covering every file in the bundle.
8. Write `reports/benchmark_bundle_index.md` and `reports/claim_evidence_ledger.md`.
9. Write top-level `README.md`.
10. If `--validate`, invoke validator on the produced bundle.

Determinism: with the same source artifacts and same git SHA, two runs to two distinct `--out-dir` paths must produce identical artifact/schema/ledger/lineage byte content (modulo `generated_at_utc`). The test suite verifies this by setting `--generated-at-utc` to a fixed sentinel for fixture comparisons.

## 13. Test plan (P5)

`tests/benchmarks/test_phase18a_benchmark_externalization.py`:

1. **schemas_are_valid_json** — load each of the 7 schemas; assert each is dict with `$schema`, `type`, `required`.
2. **exporter_writes_complete_bundle** — run exporter into tmp, assert all required files present.
3. **validator_accepts_valid_bundle** — run validator on the freshly-exported bundle, assert exit 0.
4. **validator_rejects_missing_required_file** — delete `claim_evidence_ledger.json`; expect non-zero exit, error mentions missing file.
5. **validator_rejects_checksum_mismatch** — flip a byte in one artifact post-export; expect non-zero exit.
6. **validator_rejects_unknown_claim_label** — patch ledger to set `label = "FANTASTIC"`; expect non-zero exit.
7. **validator_rejects_unresolved_evidence_reference** — patch ledger to point at non-existent artifact; expect non-zero exit.
8. **validator_rejects_raw_stdout_leakage** — re-export with `--include-raw`; assert validator rejects (`--include-raw` is opt-in for human inspection but not allowed in release-gate-pass bundles).
9. **validator_rejects_unsupported_ranking_claim** — inject `"is faster than"` substring into `reports/claim_evidence_ledger.md` post-export; expect non-zero exit.
10. **validator_rejects_provider_delta_nonzero** — patch manifest `provider_jobs_delta = 1`; expect non-zero exit.
11. **exporter_preserves_source_sha256** — assert ledger entries' `source_sha256` matches actual SHA-256 of the committed source files.
12. **claim_ledger_includes_required_claims** — assert all 16 claims from §7 are present.
13. **release_lineage_includes_v3_8_0_and_v3_9_alphas** — assert lineage matches §8.
14. **markdown_reports_generated** — assert `reports/benchmark_bundle_index.md` and `reports/claim_evidence_ledger.md` exist and reference all artifacts.
15. **deterministic_export** — run exporter twice into distinct tmp dirs with `--generated-at-utc` pinned; assert artifact + schema + ledger + lineage byte-equal.

## 14. Docker `--network none` (P9)

`.dockerignore` carve-outs:

```
!tools/run_phase18a_benchmark_externalization.py
!tools/validate_phase18a_benchmark_bundle.py
!schemas/
!tests/benchmarks/
```

Build:

```
docker build -t waggledance:phase18a -f Dockerfile .
```

Run combined export + validate:

```
docker run --rm --network none waggledance:phase18a sh -lc \
  'python tools/run_phase18a_benchmark_externalization.py --out-dir /tmp/phase18a_export_bundle --validate \
   && python tools/validate_phase18a_benchmark_bundle.py --bundle-dir /tmp/phase18a_export_bundle'
```

Both must exit 0. The container has no Ollama, no network, no cloud reachability — the export is a pure file-shuffle + checksum + validation exercise that proves the bundle is reproducible offline.

## 15. Release decision (P10)

* **Decision A — release `v3.10.0-benchmark-schema-alpha` PRERELEASE** if and only if all the following are true:
  - All 15 P5 tests pass.
  - Exporter `--validate` exits 0 on host.
  - Validator standalone exits 0.
  - Sanitized artifacts contain no raw stdout.
  - Markdown reports contain no forbidden substrings.
  - Docker `--network none` export + validate exits 0.
  - All 16 required claims present in ledger.
  - Release lineage matches §8.
  - All 5 prior tags unchanged.
  - PR-level CI green.
* **Decision B — no new release**: any of the above fails.

## 16. Sign-off

This design is the canonical contract for Phase 18A. Any deviation in the implementation must be reflected back into this document in the same PR.
