# Phase 2A-2 hand-off (proposed, not executed)

Phase 2A-1 ended with green hardening gates. This document proposes the
shape of Phase 2A-2 (Claude self-review runner). It is INTENTIONALLY a
proposal: Phase 2A-1 must not implement `Invoke-WaggleReview.ps1` or
auto-proceed to 2A-2.

## Scope of Phase 2A-2

Phase 2A-2 should add:

1. **`orchestrator\Invoke-WaggleReview.ps1`**

   A second entry point alongside `Invoke-WaggleIteration.ps1`. It
   runs Claude in a *review* role over an existing smoke iteration's
   `llm_input_package.md`. Output: a structured review on disk plus the
   usual signal/state files. Reuses the same lock, signal, redaction,
   and packager primitives.

2. **Review prompt templates**

   Three template files under `prompts/review/`:
   - `architect.md` — checks design coherence, layering, contracts.
   - `security.md` — checks for prompt-injection surfaces, redaction
     gaps, environment leaks, lock/path validation, secret handling.
   - `reliability.md` — checks for crash modes, timeout handling,
     resume semantics, signal-conflict handling, partial state.

   Each template should already include the WAGGLE COMPLETION CONTRACT
   (the orchestrator already appends one; templates can reuse). They
   should NOT include the SMOKE ARTIFACT CONTRACT — review runs do not
   write a per-iteration unique artifact.

3. **Review-mode tool profile decision (RECOMMENDATION)**

   Implement a safe config override: a separate
   `orchestrator.config.review.json` (or a `--ReviewMode` switch) that:

   - sets `safeMode=true`, `allowBash=false`, `dangerouslySkipPermissions=false`;
   - removes `Bash` from `allowedTools`, adds it to `disallowedTools`;
   - sets `requireUniqueArtifact=false` (the new P3 config field this
     phase added — see redaction_hardening.md and smoke_hardening.md);
   - leaves `sanitizeEnvironment=true`, `requireExitMarker=false`,
     `requireReport=false` for compatibility with the print-mode
     verifier;
   - preserves `iterationsDir`/`stateDir` so reviews share the same
     iteration namespace as smokes (or uses a parallel
     `iterations_review/` if that helps audit clarity — that decision
     can wait for actual usage).

   Rationale: the live `orchestrator.config.json` has
   `dangerouslySkipPermissions=true` and `allowBash=true`, both of
   which are inappropriate for an autonomous review-mode run. Reusing
   the live config would silently widen the trust boundary.

4. **Architect + Security review smoke**

   At minimum, run the Architect prompt and the Security prompt over
   the known-good package
   `iterations\2026-05-06_15-49-51\llm_input_package.md` (or a fresh
   smoke run from this hardened code) and confirm:
   - both runs reach `COMPLETED`;
   - both produce a redaction_report.json with no FP regression;
   - neither emits a tool-call to Bash;
   - the two reviews are persisted under
     `iterations\<iter>\reviews\architect.md` and
     `iterations\<iter>\reviews\security.md`.

## What Phase 2A-2 must NOT do

- No browser automation. No headless Chrome / Playwright. No webview.
- No GitHub push, no PR, no tag, no release.
- No Phase 2B auto-proceed: after the architect/security smoke is green,
  STOP and require human review before any further automation lane.
- No removal of any guardrail Phase 2A-1 added (git hygiene, redaction
  contextual allowlist, unique artifact contract, syntax preflight).
- No re-implementation of redaction or signal handling in
  `Invoke-WaggleReview.ps1` — share the existing libs.

## Pre-requisites carried over from Phase 2A-1

- `.git/info/exclude` covers orchestrator scratch dirs (P1).
- Redaction has contextual SHA allowlist (P2).
- `Test-Redaction.ps1` (27 tests) green; `Test-Redactor.ps1`
  (26 tests) green.
- `requireUniqueArtifact` config field (P3) defaults to `true` for
  smoke; review must explicitly set it to `false`.
- `Test-SmokeValidation.ps1` (16 tests) green.
- `Test-Syntax.ps1` (30 files) green and should be the FIRST gate of
  any Phase 2A-2 session.
- `Test-Redaction.ps1`, `Test-Redactor.ps1`, `Test-SmokeValidation.ps1`,
  `Test-Syntax.ps1` should all be added to a single
  `Run-WaggleHardeningGates.ps1` driver in 2A-2 (optional ergonomics).

## Open follow-ups (not blockers)

- `orchestrator/lib/*.ps1` is currently caught by the public
  `.gitignore` line `lib/`. Phase 2A-1 deliberately did not touch the
  public ignore set; Phase 2A-2 (or a separate ignore-policy iteration)
  should decide whether to add `!orchestrator/lib/` so the orchestrator
  source can be committed.
- `iterations_p5_test\` and `state_p5_test\` (P5 scratch dirs) are not
  in `.git/info/exclude` yet. Either add them or delete the dirs after
  each P5 gate. Suggested: extend the exclude with
  `/iterations_p5_test/`, `/state_p5_test/`, and
  `/orchestrator.config.p5_test.json`.
- Bash refusal is currently a soft instruction in the smoke prompt.
  PHASE2A2_HANDOFF recommends keeping `allowBash=false` /
  `disallowedTools=[Bash]` at the config level for any review run.
- The repeatability gate ran with fake-claude to avoid lock contention
  with the active Phase 2A-1 session. PHASE2A2_HANDOFF asks the
  operator to also run one real-claude smoke run from a clean session
  to catch anything fake-claude does not exercise (real CLI arg
  parsing, real auth, model selection).

## Final-report cross-references

- `baseline_inventory.md`
- `git_hygiene.md`
- `redaction_hardening.md`
- `smoke_hardening.md`
- `syntax_preflight.md`
- `repeatability_report.md`
- `final_report.md`

## DO NOT AUTO-PROCEED: human review required before Phase 2A-2.

---

## Completed by Phase 2A-2 (2026-05-06)

The Phase 2A-2 session executed the work proposed above. All four
proposed scope items shipped:

1. `orchestrator/Invoke-WaggleReview.ps1` exists, is PS 5.1 syntax-clean,
   and reuses Phase 1.6 / Phase 2A-1 primitives (Lockfile, Detector,
   Signals, Preflight, State, ClaudeRunner, Redactor, ArtifactValidator).
   It uses an in-script synchronous subprocess wrapper
   (`Invoke-WaggleReviewSubprocess`) for stdout capture instead of the
   ClaudeRunner async event path (a PS 5.1 limitation that drops
   stdout for fast-exit children; see
   `docs/runs/orchestrator_phase2a2_review_runner_2026_05_06/review_runner_validation.md`).
2. Three review prompt templates landed under `prompts/review/`:
   `architect.md`, `security.md`, `reliability.md`.
3. `orchestrator.config.review.example.json` is the safe profile
   recommended in this hand-off, with the runner additionally
   hard-clamping `allowBash=false`, `dangerouslySkipPermissions=false`,
   `requireUniqueArtifact=false`, and removing Write/Edit/Bash from
   the effective allowed-tools list.
4. Architect, security, and reliability reviews were all run for real
   over baseline iteration `2026-05-06_19-45-54`; all three reached
   `COMPLETED`, all three review JSONs validate against
   `schemas/review.schema.json`, and the safety-profile metadata is
   identical across roles.

Phase 2A-2 was shipped via PR (no tag, no release, per master prompt
GLOBAL HARD RULES). The Phase 2A-1 open follow-ups (the broad `lib/`
unignore decision and the P5 scratch dir ignores) are now resolved
in `.gitignore` and `.git/info/exclude` respectively. The Phase 2A-1
fake-claude limitation is closed: P3 of Phase 2A-2 ran a real-Claude
smoke from a clean session and reached `COMPLETED` with the unique
artifact contract honored end-to-end.

DO NOT AUTO-PROCEED: human review is required before Phase 2B / any
multi-LLM lane.
