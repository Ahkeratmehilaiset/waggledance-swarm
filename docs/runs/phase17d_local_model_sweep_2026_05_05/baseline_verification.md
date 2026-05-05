# Phase 17D — P0 Baseline Verification

**Date (UTC):** 2026-05-05
**Branch:** `phase17d/local-model-sweep`
**Worktree:** `C:/Python/project2-phase17d-local-model-sweep`
**Base:** `origin/main @ 4f8a9ea7774a9f4c862c8342dcc69ef714386b8f` (Phase 17C post-release docs PR #76 merge)
**Operator decision:** Option B — full autonomy, 10h budget

Phase 17D extends Phase 17C from one local Ollama model to a multi-model sweep with repeatability statistics. Same fail-closed semantics; PR-only landing; no model pull or download; no cloud API calls.

## 1. Tag invariants (must remain unchanged through Phase 17D)

| Tag | Target SHA | isPrerelease | Latest? |
| --- | --- | --- | --- |
| `v3.8.0` | `824176ebf2a6b8debed41982090a125cbe2ddad1` | false | **Yes — GitHub Latest** |
| `v3.9.0-producer-fabric-alpha` | `c726995c816ee4c09e031c2190c3de6592e82879` | true | No |
| `v3.9.1-local-efficiency-benchmark-alpha` | `f4d0a4a4152ca74e98a8d7f7161c233075bf4111` | true | No |
| `v3.9.2-local-ollama-baseline-alpha` | `db5d7db1ecb9ae6f17293f0bf7261f4c9d40e91c` | true | No |

`gh release list --limit 6` at session start:

```
v3.9.2-local-ollama-baseline-alpha — Phase 17C (PRERELEASE)   Pre-release   2026-05-04T22:26:28Z
v3.9.1-local-efficiency-benchmark-alpha — Phase 17B           Pre-release   2026-05-04T20:59:09Z
v3.9.0-producer-fabric-alpha — Phase 17A                       Pre-release   2026-05-04T18:32:47Z
v3.8.0 — stable release                                         Latest        2026-05-04T07:13:27Z
v3.7.8-docker-gate-alpha — Phase 16D                            Pre-release   2026-05-02T08:00:33Z
v3.7.7-stable-gate-alpha — Phase 16C                            Pre-release   2026-05-02T06:16:03Z
```

Phase 17D must NOT modify any of those four prior tags.

## 2. Local Ollama state

```
$ command -v ollama
/c/Users/mfi0jjko/AppData/Local/Programs/Ollama/ollama

$ ollama --version
ollama version is 0.22.1

$ ollama ps
(no models running)
```

22 local models present (unchanged since Phase 17C). Highlights for the Phase 17D sweep:

### 17D candidate models (rule: max 4, size ≤ ~10 GB)

| Rank | Model | Size | ID | In sweep? |
| --- | --- | --- | --- | --- |
| 1 | `gemma4:e4b` | 9.6 GB | `c6eb396dbd59` | yes |
| 2 | `gemma3:4b` | 3.3 GB | `a2af6cc3eb7f` | yes |
| 3 | `llama3.2:3b` | 2.0 GB | `a80c4f17acd5` | yes |
| 4 | `phi4-mini:latest` | 2.5 GB | `78fad5d182a7` | yes |
| 5 | `qwen2.5:7b` | 4.7 GB | `845dbda0ea48` | held back (rank-5 spillover) |

The sweep picks the first 4 already-installed models from this preference order, walking it top-down. `qwen2.5:7b` is held back because the master prompt caps `--max-models 4`. Operator can opt in via `--max-models 5` or `--models gemma4:e4b,gemma3:4b,llama3.2:3b,phi4-mini:latest,qwen2.5:7b` if they want a 5-model run.

### Deferred (NOT_RUN_TOO_LARGE_BY_DEFAULT)

| Model | Size | Reason |
| --- | --- | --- |
| `gemma4:26b` | 17 GB | size > 10 GB threshold |
| `qwen2.5:32b` | 19 GB | size > 10 GB threshold |
| `osoderholm/poro:latest` | 20 GB | size > 10 GB threshold |

These are present locally and NOT being pulled. They are excluded by default to keep the wall-clock budget reasonable on a 24-CPU laptop. An explicit `--ollama-model gemma4:26b` would let the operator opt in for one model run if they want.

## 3. Phase 17C carry-over (canonical, NOT to be re-derived in 17D)

* `v3.9.2-local-ollama-baseline-alpha` measured `gemma4:e4b` only: 30/30 prompts succeeded, median 0.7866 s, p95 17.5538 s, mean 2.5539 s, total 76.6193 s, hash chain head `3813e784f4ab42d9...`.
* Phase 17C harness `tools/run_phase17c_local_ollama_baseline.py` already implements: rule-14 model selection, 30 deterministic prompts across the six low-risk allowlist families, bytes-mode subprocess output with UTF-8 `errors="replace"` decoding, forbidden-vocabulary substring scrub. Phase 17D will reuse those primitives directly (import the prompt manifest, the decoder, the scrub).

## 4. What Phase 17D CHANGES (in scope)

1. New `tools/run_phase17d_local_model_sweep.py` aggregator that walks N models × R repeats × P prompts and emits per-model latency + repeatability statistics.
2. New `docs/benchmarks/LOCAL_OLLAMA_MODEL_SWEEP_2026.md`.
3. Upgrade of `docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md` axis J from `MEASURED-LOCAL-OLLAMA-ONE-MODEL` to `MEASURED-LOCAL-OLLAMA-PANEL` (panel of N≥2 already-installed local models, no cross-vendor ranking).
4. Update of `docs/benchmarks/LOCAL_OLLAMA_BASELINE_2026.md` with a Phase 17D pointer.
5. New `tests/autonomy_growth/test_phase17d_local_model_sweep.py`.
6. `.dockerignore` carve-out: `!tools/run_phase17d_local_model_sweep.py`.
7. Candidate-mode entries in CURRENT_STATUS.md, CHANGELOG.md, RELEASE_READINESS.md, README.md.

## 5. What Phase 17D does NOT change (out of scope, tested as invariants)

* Does NOT modify `v3.8.0`, `v3.9.0-producer-fabric-alpha`, `v3.9.1-local-efficiency-benchmark-alpha`, or `v3.9.2-local-ollama-baseline-alpha` tags.
* Does NOT change autonomy code, the six-family allowlist, control-plane schema, runtime entrypoint, or solver dispatcher.
* Does NOT execute Stage-2 atomic flip; does NOT collect HUMAN_APPROVAL.
* Does NOT touch `phase8.5/*` branches.
* Does NOT pull or download any Ollama model.
* Does NOT call any cloud LLM API.
* Does NOT introduce a stable-tagged release (at most a PRERELEASE).
* Does NOT widen forbidden-vocabulary surface; does NOT add any cross-vendor ranking; does NOT make any raw-intelligence superiority claim.
* Does NOT edit `CURRENT_STATE.md` manually (per master prompt rule 17).

## 6. Push / merge discipline

* All landings via PR — no direct push to `main`.
* `git push` not classified as failed before 180 s; verify with `git ls-remote origin <branch>` polling at 15 s intervals.
* Autonomous squash-merge only with `gh pr merge --match-head-commit` against EXPECTED_HEAD; never `--admin`, never `--no-verify`, never force-push.

## 7. Result of P0

Baseline verification PASS. Proceeding to P1 (`benchmark_design.md`) before any code is written.
