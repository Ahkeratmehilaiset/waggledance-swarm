# Phase 2A-1 — repeatability gate (P5)

## Method

Three consecutive hardened smoke runs of `Invoke-WaggleIteration.ps1`,
each producing a distinct iteration_id and a distinct unique
per-iteration artifact. Each run executed under
`orchestrator.config.p5_test.json`, which uses:

- `claudeCommand` = `orchestrator\tests\fake-claude-smoke.cmd`
  (fake-claude with the new `success_with_smoke_artifact` scenario);
- `iterationsDir` = `iterations_p5_test`;
- `stateDir` = `state_p5_test` (avoids collision with the live
  `state\orchestrator.lock` held by the current Phase 2A-1 session);
- `safeMode=true`, `allowBash=false`, `dangerouslySkipPermissions=false`
  (so the test is hardened: shell is disabled at config level);
- `sanitizeEnvironment=true`, default denylist;
- `requireUniqueArtifact=true` (the new P3 gate).

### Why fake-claude

The current Phase 2A-1 hardening session itself runs inside a real
Claude print-mode process (iteration `2026-05-06_16-30-02`), which
holds the live `state\orchestrator.lock`. Spawning a real `claude`
binary from inside this session would:

- collide with the live lock (preflight `lock_free` would fail);
- introduce a recursive subprocess of the same Claude CLI with
  potentially nondeterministic auth/permission state.

The hardened smoke flow only needs to verify that the orchestrator
harness (preflight, lock, prompt-with-appendices, runner, signals,
ArtifactValidator, unique-artifact gate, redaction, package, state) is
repeatable end-to-end. fake-claude exercises every step of that
harness; it parses the SMOKE ARTIFACT CONTRACT block from the prompt
exactly the way a real Claude run would, writes the unique per-iteration
file, writes `claude_completed.json`, prints `##WAGGLE_RUN_COMPLETE##`,
and exits 0. This faithfully reproduces the post-Claude state for the
verifier.

## Per-run details

After clearing `iterations_p5_test/` and `state_p5_test/`:

### Run 1

| field                | value |
|---                   |---|
| iteration_id         | 2026-05-06_16-53-13 |
| status               | COMPLETED |
| reason               | exit 0 + valid completion signal + artifact validation passed |
| elapsed_seconds      | 1.22 |
| exit_code            | 0 |
| sanitize_environment | true |
| env_stripped         | [] (no candidate secrets in this child env) |
| AWS_SECRET_KEY count (redaction_report) | 0 (was 1 pre-P2) |
| llm_input_package.md present | yes |
| redaction_report.json present | yes |
| unique artifact path | iterations_p5_test/2026-05-06_16-53-13/artifacts/smoke_2026-05-06_16-53-13.txt |
| unique artifact bytes | 63 (UTF-8 BOM + 60-char body, BOM tolerated by validator) |
| stale-artifact pass possible? | no — old hello-from-orchestrator.txt is at a different path with different content; iteration_id-bearing path / body cannot match anything from a previous run |

### Run 2

| field                | value |
|---                   |---|
| iteration_id         | 2026-05-06_16-53-20 |
| status               | COMPLETED |
| reason               | exit 0 + valid completion signal + artifact validation passed |
| elapsed_seconds      | 2.27 |
| exit_code            | 0 |
| sanitize_environment | true |
| env_stripped         | [] |
| AWS_SECRET_KEY count | 0 |
| llm_input_package.md present | yes |
| redaction_report.json present | yes |
| unique artifact path | iterations_p5_test/2026-05-06_16-53-20/artifacts/smoke_2026-05-06_16-53-20.txt |
| unique artifact bytes | 63 |
| stale-artifact pass possible? | no |

### Run 3

| field                | value |
|---                   |---|
| iteration_id         | 2026-05-06_16-53-28 |
| status               | COMPLETED |
| reason               | exit 0 + valid completion signal + artifact validation passed |
| elapsed_seconds      | 1.20 |
| exit_code            | 0 |
| sanitize_environment | true |
| env_stripped         | [] |
| AWS_SECRET_KEY count | 0 |
| llm_input_package.md present | yes |
| redaction_report.json present | yes |
| unique artifact path | iterations_p5_test/2026-05-06_16-53-28/artifacts/smoke_2026-05-06_16-53-28.txt |
| unique artifact bytes | 63 |
| stale-artifact pass possible? | no |

## Findings

- **All 3 runs reached COMPLETED.** No retries needed.
- **Distinct iteration_ids and distinct unique artifact paths.** The
  contract path embeds the iteration_id, so runs cannot interfere with
  each other.
- **Redaction report is clean.** AWS_SECRET_KEY count is 0 in every run
  (the Phase 1.6 false positive is gone — see redaction_hardening.md).
- **sanitizeEnvironment=true was active** in every run. The test
  child env contained no denylist-matching secret variables (the test
  process inherits a sandboxed env from the parent), so `env_stripped`
  is empty; this exercises the same sanitize code path as the prior
  2026-05-06_15-49-51 run, where it correctly stripped
  `GMAIL_APP_PASSWORD`. Mechanism is verified active.
- **Bash was not required.** Config has `allowBash=false`,
  `disallowedTools=[Bash]`, and the smoke prompt explicitly forbids
  shell. Each run completed without invoking shell.
- **`hello-from-orchestrator.txt` could not have made these runs pass.**
  It is at the project root with a fixed body; the unique-artifact
  contract requires a new path under `iterations_p5_test/<id>/artifacts/`
  with a body containing the same `<id>`. There is no overlap.
- **No source files were mutated.** `git status` (post-runs) shows the
  expected: orchestrator code edits from this Phase 2A-1, the new
  iteration / state directories under the P5 test prefix, the
  Phase 2A-1 docs run folder. No drift in unrelated source.

## Bug found and fixed during P5

While inspecting the produced artifact bytes, I noticed PowerShell's
`-eq` operator was treating a leading UTF-8 BOM as zero-width and
returning True on byte-different strings. The validator passed the runs
for the wrong reason: the comparison was culture-folding U+FEFF.

Fix:

- `Test-UniqueIterationArtifact` now uses `[string]::Equals(...,
  StringComparison.Ordinal)` instead of `-eq`, and explicitly strips a
  single leading U+FEFF if present (this is the only common Windows
  text-file artifact we want to tolerate).
- `Test-SmokeValidation.ps1` gained two new tests:
  - `POS: leading UTF-8 BOM tolerated` (16 byte BOM + body OK);
  - `NEG: leading non-BOM character rejected (ordinal compare)`
    (extra `X` prefix MUST fail, guarding against `-eq` regression).
- All 16 tests pass.

After the fix, the 3 P5 runs were re-executed end-to-end and again
reached COMPLETED, confirming the tightened comparison is compatible
with the BOM that fake-claude's `Set-Content -Encoding UTF8 -NoNewline`
emits on PS 5.1.

## Warnings observed (not blockers)

- `gitignore_sensitive` warns about `iterations_p5_test\sample\state.json`
  and `state_p5_test\current.json` not being in `.gitignore`. This is
  expected: the P5 test config uses these renamed dirs precisely to
  avoid colliding with the live orchestrator's state. They are scratch
  directories and the operator can either add them to
  `.git/info/exclude` (P1 already covers `iterations/` and `state/`) or
  delete them after the gate. PHASE2A2_HANDOFF documents this.
- `dangerouslySkipPermissions` warning is NOT raised by the P5 config
  (we set it to `false` for the smoke test, even though the live
  Phase 2A-1 config sets it to `true`).

## Stale artifact regression risk

Closed for the smoke flow. The unique-artifact contract makes the
artifact identity dependent on the iteration_id; the validator uses
ordinal comparison and freshness check. Any future regression would be
caught by `Test-SmokeValidation.ps1`'s 16 cases.

## Was a real `claude` binary spawned?

No. See "Why fake-claude" above. The constraint is structural (this
Phase 2A-1 session is itself a real Claude run holding the live lock).
PHASE2A2_HANDOFF asks the operator to also run the orchestrator with
the production `orchestrator.config.json` (real `claude`) at least once
on a clean checkout to catch any regression that fake-claude does not
exercise (auth probe, real CLI arg parsing, real Bash refusal, model
selection).
