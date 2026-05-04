# Phase 17C — P0 Baseline Verification

**Date (UTC):** 2026-05-04
**Branch:** `phase17c/local-ollama-baseline`
**Worktree:** `C:/Python/project2-phase17c-local-ollama-baseline`
**Base:** `origin/main @ 27b8175efe0f75088bf9f3771d54f713c3c3133c` (Phase 17B post-release docs PR #74 merge)
**Operator decision:** Option B — full autonomy

This document captures the pre-execution snapshot for Phase 17C. Phase 17C upgrades
the Phase 17B Ollama baseline track from `SKIPPED_OPTIONAL` to `MEASURED` using only
already-installed local Ollama models. No model pull, no model download, no cloud
API calls, no stable tag, no Stage-2 cutover.

## 1. Tag invariants (must remain unchanged through Phase 17C)

| Tag | Target SHA | isPrerelease | Latest? |
| --- | --- | --- | --- |
| `v3.8.0` | `824176ebf2a6b8debed41982090a125cbe2ddad1` | false | **Yes — GitHub Latest** |
| `v3.9.0-producer-fabric-alpha` | `c726995c816ee4c09e031c2190c3de6592e82879` | true | No |
| `v3.9.1-local-efficiency-benchmark-alpha` | `f4d0a4a4152ca74e98a8d7f7161c233075bf4111` | true | No |

Phase 17C must NOT modify any of those three tags. Verified at session start via
`gh release list --limit 5`:

```
v3.9.1-local-efficiency-benchmark-alpha — Phase 17B   Pre-release   2026-05-04T20:59:09Z
v3.9.0-producer-fabric-alpha — Phase 17A              Pre-release   2026-05-04T18:32:47Z
v3.8.0 — stable release                               Latest        2026-05-04T07:13:27Z
v3.7.8-docker-gate-alpha — Phase 16D                  Pre-release   2026-05-02T08:00:33Z
v3.7.7-stable-gate-alpha — Phase 16C                  Pre-release   2026-05-02T06:16:03Z
```

## 2. Local Ollama state

```
$ command -v ollama
/c/Users/mfi0jjko/AppData/Local/Programs/Ollama/ollama

$ ollama --version
ollama version is 0.22.1

$ ollama ps
(no models running)
```

22 local models present. Highlights relevant to Phase 17C selection:

| Model | Size | ID | Relevance |
| --- | --- | --- | --- |
| `gemma4:e4b` | 9.6 GB | `c6eb396dbd59` | **Preferred (rule 14)** — newer Gemma family, mid-size local |
| `gemma4:26b` | 17 GB | `5571076f3d70` | Larger Gemma — fallback if e4b unsuitable |
| `gemma3:4b` | 3.3 GB | `a2af6cc3eb7f` | Smaller Gemma — second fallback |
| `qwen2.5:7b` | 4.7 GB | `845dbda0ea48` | Qwen alternative |
| `phi4-mini:latest` | 2.5 GB | `78fad5d182a7` | Microsoft Phi alternative |
| `llama3.2:3b` | 2.0 GB | `a80c4f17acd5` | Llama alternative |

Full inventory written to `local_model_inventory.json` and `local_model_inventory.md`
in P2.

## 3. Phase 17B carry-over numbers (canonical, NOT to be re-derived in 17C)

These numbers come from `docs/runs/phase17b_local_efficiency_benchmark_2026_05_04/`
and remain authoritative:

* Six-family low-risk allowlist: scalar_unit_conversion, lookup_table,
  threshold_rule, interval_bucket_classifier, linear_arithmetic,
  bounded_interpolation.
* Phase 17A canonical corpus = 128 seeds (104 + 24 producer-fabric ports).
* Phase 17A 10k synthetic-scale proof: `is_synthetic_scale=true,
  not_canonical_corpus=true`.
* Phase 17A producer fabric port: 14 stdlib-only modules from
  `origin/phase8.5/hive-proposes`.
* Test count baseline at v3.9.1: per Phase 17B benchmark output JSON.

## 4. What Phase 17C CHANGES (in scope)

1. New `tools/run_phase17c_local_ollama_baseline.py` aggregator that wraps the
   Phase 17B WaggleDance tracks and adds a 30-prompt deterministic Ollama probe
   against `gemma4:e4b` (preferred selection from rule 14).
2. New `docs/benchmarks/LOCAL_OLLAMA_BASELINE_2026.md`.
3. Upgrade of `docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md` axis J from
   `MEASURED-LOCAL-OLLAMA-DETECT-ONLY` to
   `MEASURED-LOCAL-OLLAMA-ONE-MODEL`.
4. Addendum to `docs/benchmarks/LOCAL_EFFICIENCY_BENCHMARK_2026.md` pointing at the
   17C track.
5. New `tests/autonomy_growth/test_phase17c_local_ollama_baseline.py` covering both
   the MEASURED path and the `NOT_AVAILABLE_NOT_RUN` path via a fake-PATH fixture.
6. `.dockerignore` carve-out: `!tools/run_phase17c_local_ollama_baseline.py`.
7. Candidate-mode entries in CURRENT_STATUS.md, CHANGELOG.md,
   docs/release/RELEASE_READINESS.md, README.md (post-release will rewrite to
   PRERELEASE wording in P17C-12 if release lands).

## 5. What Phase 17C does NOT change (out of scope, tested as invariants)

* Does NOT modify `v3.8.0`, `v3.9.0-producer-fabric-alpha`, or
  `v3.9.1-local-efficiency-benchmark-alpha` tags.
* Does NOT change autonomy code, the six-family allowlist, control-plane schema,
  runtime entrypoint, or solver dispatcher.
* Does NOT execute Stage-2 atomic flip; does NOT collect HUMAN_APPROVAL.
* Does NOT touch `phase8.5/*` branches.
* Does NOT pull or download any Ollama model (offline-by-policy invariant).
* Does NOT call any cloud LLM API (Anthropic, OpenAI, Google, etc.).
* Does NOT introduce a stable-tagged release — Phase 17C may at most produce a
  PRERELEASE (`v3.9.2-local-ollama-baseline-alpha`).
* Does NOT widen forbidden-vocabulary surface (denylist still enforced).
* Does NOT change Phase 17B benchmark output JSON or its Markdown rendering for
  prior runs.

## 6. Forbidden vocabulary (substring-checked in JSON + MD outputs)

`conscious, sentient, aware, alive, AGI, revolutionary, magical, human-like mind,
self-aware, explosive intelligence, emergent, beats all competitors, world's best,
world's fastest`

Disclaimer flags belong only to JSON fields. Prose paraphrases around these
substrings.

## 7. Push / merge discipline (CLAUDE.md rules 6/7/9)

* All landings via PR — no direct push to `main`.
* `git push` not classified as failed before 180s; verify with
  `git ls-remote origin <branch>` polling at 15s intervals.
* Autonomous squash-merge only with `gh pr merge --match-head-commit` against
  EXPECTED_HEAD; never `--admin`, never `--no-verify`, never force-push.

## 8. Result of P0

Baseline verification PASS. Proceeding to P1 (`benchmark_design.md`) before any
code is written.
