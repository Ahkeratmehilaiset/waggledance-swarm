# Phase 17B — Release Decision

**Date:** 2026-05-04
**Branch:** `phase17b/local-efficiency-benchmark`
**Decision authority:** Phase 17B master prompt P9

## Decision: **A. v3.9.1-local-efficiency-benchmark-alpha PRERELEASE candidate**

The Phase 17B session selects **option A** from the master prompt's two release-decision paths.

The prerelease tag is created **only after** the Phase 17B PR merges, post-merge benchmark reproduces on `origin/main`, and the post-merge fresh-clone reproduction confirms the artifact set on `origin/main`. This document captures the branch-level evidence supporting the candidate status.

## Evidence supporting A

All branch-side gates required for `v3.9.1-local-efficiency-benchmark-alpha` (PRERELEASE) are PASS:

| gate | status | evidence |
|---|---|---|
| Benchmark harness produces JSON + Markdown | **PASS** | `phase17b_local_efficiency_benchmark.{json,md}` written to session folder; both have master-prompt-mandated keys |
| Required tracks A–E all pass | **PASS** | each underlying proof exits 0; `release_gate_pass = true` |
| 10k synthetic capability scale: 1000/1000 hits, 0 FIFO fallback, 0 miss | **PASS** | scenario B JSON: `lookup_capability_hits_total = 1000`, `lookup_fifo_fallback_total = 0`, `lookup_miss_total = 0`, `lookup_by_source = {"auto_promoted_solver": 1000}` |
| Canonical corpus ≥ 128 | **PASS** | scenario A / C / D `corpus_total = 128` |
| Producer fabric still emits 68 IR objects | **PASS** | scenario E `ir_objects_emitted_total = 68` |
| Provider/builder delta = 0 across all WD tracks | **PASS** | top-level `provider_jobs_delta = builder_jobs_delta = 0` |
| Forbidden vocabulary absent in benchmark MD body | **PASS** | substring regression test passes; harness emits `forbidden_claims_absent = true` and lists denylist in JSON for auditability |
| Optional Ollama track does not fail required gates | **PASS** | `--skip-ollama` set in default run; status `SKIPPED`; rule 14 honored |
| External competitor slots documented as NOT_RUN | **PASS** | six slots emitted with `status = "NOT_RUN"` + `reason_not_run` + `requirements_to_upgrade_to_measured` |
| Docker `--network none` reproduces | **PASS** | `docker_phase17b_verification.md` |
| Targeted tests pass | **PASS** | 17/17 Phase 17B tests in 23 min; 268 carry-forward autonomy_growth + phase10 tests still green |
| No allowlist widening | **PASS** | benchmark harness adds zero family; 128-seed library unchanged |
| No Stage-2 atomic flip | **PASS** | not executed; `docs/atomic_flip_prep/03_HUMAN_APPROVAL.yaml` not touched |
| No HUMAN_APPROVAL collected this session | **PASS** | (CLAUDE.md rule 10) |
| No new high-risk autonomy variant | **PASS** | the six in `HIGH_RISK_VARIANTS_DEFERRED.md` remain explicitly deferred |
| No actuator autonomy added | **PASS** | benchmark harness is read-only against the test fixtures; no policy/runtime/config write |
| No provider HTTP adapter added | **PASS** | only the local Ollama CLI is shelled out (and only when `--include-ollama`) |
| No `/api/autonomy/query` HTTP route | **PASS** | not introduced |
| No consciousness claim | **PASS** | competitive evidence matrix re-asserts NOT CLAIMED on raw intelligence; benchmark JSON sets `no_consciousness_claim = true` |
| v3.8.0 stable + v3.9.0-producer-fabric-alpha untouched | **PASS** | will be re-verified at P12 post-tag |

## Gates pending post-merge (NOT branch-side)

These are intentionally not verified on the branch; they are verified in P11 (post-merge):

| gate | when | what it verifies |
|---|---|---|
| PR-level CI green on phase17b/local-efficiency-benchmark | P10 | full pytest suite at PR-level CI (3 Python versions + unified) |
| Post-merge benchmark reproduces on `origin/main` | P11 | `git checkout --detach origin/main` runs `tools/run_phase17b_local_efficiency_benchmark.py` |
| Post-merge Docker rebuild reproduces | P11 | `docker build -t waggledance:v3.9.1-local-efficiency-benchmark-alpha-rc . && docker run --rm --network none ...` |
| Post-merge fresh clone reproduces | P11 | tmpdir clone from GitHub HTTPS at post-merge SHA reruns the harness at small scale |
| Annotated tag points at post-merge main SHA | P12 | `git rev-parse v3.9.1-local-efficiency-benchmark-alpha^{}` matches `git rev-parse origin/main` after merge |
| GitHub release metadata correct | P12 | `gh release view v3.9.1-local-efficiency-benchmark-alpha --json` shows `isPrerelease=true` |
| v3.8.0 still GitHub Latest | P12 | `gh release list` shows `v3.8.0 — stable release` with `Latest` flag; new prerelease appears as `Pre-release` |

## Why not B (NO TAG)?

Option B is the documented response when:

* Benchmark harness cannot produce JSON + Markdown — both are produced.
* Canonical corpus benchmark fails — passes (128/128 across A/C/D).
* 10k scale benchmark fails — passes (1000/1000 capability hits).
* Provider/builder delta != 0 — both are 0 across all WaggleDance tracks.
* Docker `--network none` fails — passes (`docker_phase17b_verification.md`).
* Fresh clone fails — not yet attempted (P11).
* CI fails — not yet attempted (P10).
* Docs make unsupported claims — substring-regression-tested clean.
* Optional Ollama tries to download a model — never (rule 14 enforced; default `SKIPPED`).
* Any cloud API would be required — none.
* Any rule violation — none.
* 10 h wall-clock budget exceeded — within budget.

None of those triggers fire. Option B is not appropriate.

## Tag plan (P12)

If P11 post-merge verification passes:

```
git fetch origin main --tags
git checkout --detach origin/main
git tag -a v3.9.1-local-efficiency-benchmark-alpha -m "v3.9.1-local-efficiency-benchmark-alpha — Phase 17B local efficiency benchmark prerelease"
git push origin v3.9.1-local-efficiency-benchmark-alpha
gh release create v3.9.1-local-efficiency-benchmark-alpha --prerelease --title "v3.9.1-local-efficiency-benchmark-alpha — Phase 17B" --notes-file <release_notes.md>
```

`--prerelease` flag REQUIRED. v3.8.0 must remain GitHub Latest after the new prerelease publishes.

## Stable v3.9.x?

**FORBIDDEN this session.** Master prompt rule 3: "NO STABLE TAGS." The only optional tag is `v3.9.1-local-efficiency-benchmark-alpha` as a PRERELEASE per rule 4.

## Stable gate ledger final pre-PR state

* PASS at branch tip: every Phase 17B branch-side criterion (see table above).
* PENDING post-merge / CI: PR-level CI, post-merge benchmark + Docker + fresh clone, tag target, release metadata, GitHub Latest.
* FAIL: 0.
* Decision: **A — proceed to PR creation, autonomous merge if guardrails pass, post-merge verification, and conditional v3.9.1-local-efficiency-benchmark-alpha PRERELEASE creation.**
