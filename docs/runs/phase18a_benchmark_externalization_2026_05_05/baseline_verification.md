# Phase 18A — P0 Baseline Verification

**Date (UTC):** 2026-05-05
**Branch:** `phase18a/benchmark-externalization-schema`
**Worktree:** `C:/Python/project2-phase18a-benchmark-externalization`
**Base:** `origin/main @ b9dd6b7d9e55d3c9d1543a4a1409af0923ed34a8` (Phase 17D post-release docs PR #78 merge)
**Operator decision:** Option B — full autonomy, 10h budget

Phase 18A externalizes Phase 17B / 17C / 17D benchmark artifacts into a versioned, validated, offline-exportable evidence bundle. This is a benchmark externalization + schema hardening sprint, not a new measurement sprint.

## 1. Tag invariants (must remain unchanged through Phase 18A)

| Tag | Target SHA | isPrerelease | Latest? |
| --- | --- | --- | --- |
| `v3.8.0` | `824176ebf2a6b8debed41982090a125cbe2ddad1` | false | **Yes — GitHub Latest** |
| `v3.9.0-producer-fabric-alpha` | `c726995c816ee4c09e031c2190c3de6592e82879` | true | No |
| `v3.9.1-local-efficiency-benchmark-alpha` | `f4d0a4a4152ca74e98a8d7f7161c233075bf4111` | true | No |
| `v3.9.2-local-ollama-baseline-alpha` | `db5d7db1ecb9ae6f17293f0bf7261f4c9d40e91c` | true | No |
| `v3.9.3-local-model-sweep-alpha` | `d0704efe46be18d480ed425ff83b087cd36ef9bd` | true | No |

`gh release list --limit 6` at session start:

```
v3.9.3-local-model-sweep-alpha — Phase 17D (PRERELEASE)        Pre-release   2026-05-05T06:05:30Z
v3.9.2-local-ollama-baseline-alpha — Phase 17C (PRERELEASE)    Pre-release   2026-05-04T22:26:28Z
v3.9.1-local-efficiency-benchmark-alpha — Phase 17B            Pre-release   2026-05-04T20:59:09Z
v3.9.0-producer-fabric-alpha — Phase 17A                        Pre-release   2026-05-04T18:32:47Z
v3.8.0 — stable release                                          Latest        2026-05-04T07:13:27Z
v3.7.8-docker-gate-alpha — Phase 16D                             Pre-release   2026-05-02T08:00:33Z
```

Phase 18A must NOT modify any of these five tags.

## 2. Source benchmark artifacts (the bundle's input)

All three present and committed:

* `docs/runs/phase17b_local_efficiency_benchmark_2026_05_04/phase17b_local_efficiency_benchmark.json`
* `docs/runs/phase17c_local_ollama_baseline_2026_05_04/phase17c_local_ollama_baseline.json`
* `docs/runs/phase17d_local_model_sweep_2026_05_05/phase17d_local_model_sweep.json`

Each has a sibling `.md` rendered report. Phase 18A imports the JSONs, sanitizes them, validates them against a schema, and packages them with a manifest, claim ledger, release lineage, and SHA-256 checksums.

## 3. Open PRs / CI status

* 5 open Dependabot PRs (cachetools, av, scipy, actions/checkout, actions/setup-python). Out of Phase 18A scope; not touched.
* Recent main push-event CI: green (PR #76, #77, #78 all merged with all checks PASS).

## 4. What Phase 18A CHANGES (in scope)

1. **`schemas/benchmarks/v1/`** — 7 JSON Schema files describing the bundle, artifact index, claim ledger, release lineage, and the three source-artifact shapes.
2. **`tools/validate_phase18a_benchmark_bundle.py`** — stdlib-only validator (no `jsonschema` dep) that enforces required keys, types, enums, checksum verification, claim-label allowlist, and forbidden-substring scrubs on the rendered Markdown.
3. **`tools/run_phase18a_benchmark_externalization.py`** — exporter that ingests Phase 17B/17C/17D JSONs, sanitizes them (raw stdout fields → redaction stubs), writes manifest + artifact index + claim ledger + release lineage + checksums + Markdown reports, optionally invokes the validator.
4. **`tests/benchmarks/test_phase18a_benchmark_externalization.py`** — fixture-based tests for both happy path and adversarial bundles (missing files, checksum mismatch, raw stdout leakage, forbidden labels, unsupported claims).
5. **`docs/benchmarks/BENCHMARK_ARTIFACT_SCHEMA.md`**, **`BENCHMARK_EVIDENCE_BUNDLE_2026.md`**, **`CLAIM_EVIDENCE_LEDGER.md`** — public-facing docs.
6. Update of `COMPETITIVE_EVIDENCE_MATRIX_2026.md` — add a new axis "Benchmark artifact externalization / schema validation" labelled `PROVEN` if export+validation passes; raw-intelligence row remains `NOT CLAIMED`.
7. Candidate-mode entries in CURRENT_STATUS.md, CHANGELOG.md, RELEASE_READINESS.md, README.md.
8. `.dockerignore` carve-outs for the new exporter, validator, schemas/ tree, and tests/benchmarks/.

## 5. What Phase 18A does NOT change (out of scope, tested as invariants)

* Does NOT modify any of the 5 prior tags. v3.8.0 remains GitHub Latest.
* Does NOT change autonomy code, the six-family allowlist, control-plane schema, runtime entrypoint, solver dispatcher, or any benchmark *measurement*.
* Does NOT execute Stage-2 atomic flip; does NOT collect HUMAN_APPROVAL.
* Does NOT touch `phase8.5/*` branches.
* Does NOT pull or download any Ollama model.
* Does NOT call any cloud LLM API.
* Does NOT introduce a stable-tagged release (at most a PRERELEASE).
* Does NOT widen forbidden-vocabulary surface; does NOT add any cross-vendor ranking; does NOT make any raw-intelligence superiority claim.
* Does NOT edit `CURRENT_STATE.md` manually (master prompt rule 23 — separate from `CURRENT_STATUS.md`).
* Does NOT rerun the full Phase 17D 4-model sweep — the committed Phase 17D JSON is the source of evidence.
* Does NOT add new pip dependencies (validator is stdlib-only).

## 6. Push / merge discipline

* All landings via PR — no direct push to `main`.
* Autonomous squash-merge only with `gh pr merge --match-head-commit` against EXPECTED_HEAD; never `--admin`, never `--no-verify`, never force-push.
* Fresh-clone retest before merge per master prompt P11.

## 7. Result of P0

Baseline verification PASS. Proceeding to P1 (`benchmark_externalization_design.md`) before any code is written.
