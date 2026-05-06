# Phase 2A-1 final report

Iteration: `2026-05-06_16-30-02`
Branch: `main` (no commits, no tags, no PRs created — local-only)
Result: **COMPLETED — Phase 2A-1 hardening gates green; Phase 2A-2 not started.**

## Per-phase results

| Phase | Status | Artefact |
|---|---|---|
| P0 baseline inventory | green | baseline_inventory.md |
| P1 git hygiene (`.git/info/exclude`) | green | git_hygiene.md |
| P2 redaction hardening (SHA FP fix) | green | redaction_hardening.md |
| P3 unique smoke artifact validation | green | smoke_hardening.md |
| P4 PowerShell 5.1 syntax preflight | green | syntax_preflight.md |
| P5 repeatability gate (3 runs) | green | repeatability_report.md |
| P6 finalisation | green | this file + PHASE2A2_HANDOFF.md |

## Tests added / changed

- `orchestrator\Test-Redaction.ps1` (NEW) — 27 tests covering
  contextual SHA allowlist (preserve) and continued real-secret
  detection (gho_, ghp_, Bearer, password=, private key, AWS_SECRET_KEY
  in non-SHA contexts).
  Result: 27/27 PASS.
- `orchestrator\Test-SmokeValidation.ps1` (NEW) — 16 tests covering
  unique-iteration-artifact validator: missing, stale path, wrong
  content, stale mtime, oversize, NUL byte, success, trailing-LF
  tolerance, BOM tolerance, ordinal-compare regression guard.
  Result: 16/16 PASS.
- `orchestrator\Test-Syntax.ps1` (NEW) — parser-API preflight over
  every `.ps1`/`.psm1` under `orchestrator\`.
  Result: 30/30 PASS.
- `orchestrator\Test-Redactor.ps1` — unchanged.
  Result (post-P2): 26/26 PASS, no regression.

## Files changed

Source:

- `orchestrator\lib\Redactor.ps1` — added Protect/Restore-GitShaContexts
  with contextual rules; rewired `Invoke-WaggleRedaction` to protect
  before count/redact and restore after; closure binding of sentinel
  prefix/suffix to local variables (script-scope vars are not reliably
  visible in MatchEvaluator scriptblocks).
- `orchestrator\lib\ArtifactValidator.ps1` — added
  `Test-UniqueIterationArtifact` (P3 contract) with size, freshness,
  no-NUL, strict-UTF-8, BOM-tolerant ordinal content compare.
- `orchestrator\lib\CompletionVerifier.ps1` — `Resolve-PrintModeVerdict`
  gained optional `UniqueArtifactPath`/`UniqueArtifactBody`/
  `UniqueArtifactMaxBytes` parameters and a final gate that downgrades
  to `COMPLETED_UNVERIFIED` on missing/wrong unique artifact.
- `orchestrator\Invoke-WaggleIteration.ps1` — computes per-iteration
  unique-artifact path + body, injects a SMOKE ARTIFACT CONTRACT block
  into the prompt appendix (gated by config `requireUniqueArtifact`,
  default true), wires the new params into the verdict via splat.
- `orchestrator\tests\fake-claude.ps1` — new
  `success_with_smoke_artifact` scenario; parses contract from prompt.
- `orchestrator\tests\fake-claude-smoke.cmd` (NEW) — wrapper that
  answers preflight `--version` and `auth status`, then forwards stdin
  into fake-claude.ps1 with the smoke scenario.

Tests:

- `orchestrator\Test-Redaction.ps1` (NEW)
- `orchestrator\Test-SmokeValidation.ps1` (NEW)
- `orchestrator\Test-Syntax.ps1` (NEW)

Prompts / configs:

- `prompts\smoke.md` — replaced with high-level intent file
  (orchestrator now fills in unique path + body).
- `orchestrator.config.p5_test.json` (NEW, local-only) — P5 test config
  with fake-claude wrapper, distinct stateDir/iterationsDir.

Local-only ignores:

- `C:\Python\project2\.git\info\exclude` — appended Phase 2A-1
  patterns. NOT a checked-in change.

Docs:

- `docs\runs\orchestrator_phase2a1_hardening_2026_05_06\baseline_inventory.md`
- `docs\runs\orchestrator_phase2a1_hardening_2026_05_06\git_hygiene.md`
- `docs\runs\orchestrator_phase2a1_hardening_2026_05_06\redaction_hardening.md`
- `docs\runs\orchestrator_phase2a1_hardening_2026_05_06\smoke_hardening.md`
- `docs\runs\orchestrator_phase2a1_hardening_2026_05_06\syntax_preflight.md`
- `docs\runs\orchestrator_phase2a1_hardening_2026_05_06\repeatability_report.md`
- `docs\runs\orchestrator_phase2a1_hardening_2026_05_06\PHASE2A2_HANDOFF.md`
- `docs\runs\orchestrator_phase2a1_hardening_2026_05_06\final_report.md`
  (this file)

## Warnings remaining

- `orchestrator/lib/*` is shadowed by the public `.gitignore` `lib/`
  pattern (pre-existing, NOT introduced by P1). Decision deferred to
  Phase 2A-2 or a separate ignore-policy iteration. See
  PHASE2A2_HANDOFF for the recommended fix.
- P5 scratch dirs `iterations_p5_test\`, `state_p5_test\`, and the
  `orchestrator.config.p5_test.json` config are NOT in `.git/info/exclude`
  yet. They are local scratch and should either be deleted or added
  to the exclude file after this iteration.
- The hardened smoke ran with fake-claude due to lock contention with
  the live Phase 2A-1 session. The next operator-driven session should
  run one real-claude smoke from a clean session to catch anything
  fake-claude does not exercise.

## Known limitations

- The contextual SHA allowlist only covers six common contexts (JSON
  field, JSON nested oid, YAML field, KV form, `commit <sha>` log line,
  `sha:`/`sha=` bare). A 40-hex SHA in unstructured prose still gets
  redacted as AWS_SECRET_KEY. We accept this in exchange for keeping
  AWS detection strong.
- The unique-artifact contract is a soft instruction in the prompt;
  the validator is the hard check. Bash refusal is also instructional.
- `requireUniqueArtifact` is a new optional config field. The live
  `orchestrator.config.json` does not set it explicitly; the default
  is `true`. Phase 2A-2's review runner must set it to `false`.
- Trailing-newline tolerance is exactly one LF/CR/CRLF; leading-BOM
  tolerance is exactly one U+FEFF. Anything else fails ordinal compare.
- StrictMode Latest patterns were not introduced into new files (per
  Phase 1.6 lessons).

## What this iteration did not do

- **No review runner implemented.** `Invoke-WaggleReview.ps1` does NOT
  exist after this iteration. PHASE2A2_HANDOFF specifies how to add it.
- **No git push.** The repo's `git status` shows only local changes.
- **No PR created.**
- **No tag or release.**
- **No browser automation.**
- **No `gh auth token`, `gh auth git-credential get`, or any command
  that would print credentials.** No tokens appear anywhere in any
  output, log, or report file produced by this iteration.
- **No commit or stage.** All work is loose in the working tree.
- **No modification of any product release tag.**
- **No new pip / npm dependencies.**

## Exact next human decision

The operator should:

1. Read `final_report.md` (this file) and `PHASE2A2_HANDOFF.md`.
2. Optionally run a one-shot smoke from a CLEAN session (i.e. not
   inside an active Claude run) using the live
   `orchestrator.config.json` and the new `prompts\smoke.md` to confirm
   the real-claude path also reaches COMPLETED with the unique artifact
   contract. If green, that closes the "did fake-claude miss anything?"
   open question.
3. Decide whether to commit the orchestrator source changes (gated on
   the `lib/` ignore decision in PHASE2A2_HANDOFF).
4. Approve the Phase 2A-2 scope as proposed in PHASE2A2_HANDOFF.
5. Open a fresh session for Phase 2A-2 (a new iteration of
   `Invoke-WaggleIteration.ps1`) to start the review-runner work.

DO NOT AUTO-PROCEED: human review is required before Phase 2A-2.
