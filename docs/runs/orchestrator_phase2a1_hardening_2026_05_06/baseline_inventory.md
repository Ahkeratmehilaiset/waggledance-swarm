# Phase 2A-1 baseline inventory

Generated: 2026-05-06 (post Phase 1.6 smoke green).
Purpose: pre-hardening snapshot before P1-P5 changes.

## Project root

`C:\Python\project2-master` (persistent C-drive, matches CLAUDE.md golden rule 1).

## Orchestrator files found

```
orchestrator\Invoke-WaggleIteration.ps1     (entry point, print mode)
orchestrator\Watch-ClaudeCode.ps1
orchestrator\Start-WaggleSession.ps1
orchestrator\README.md
orchestrator\TECHNICAL_PLAN.md
orchestrator\CHANGELOG.md
orchestrator\config.example.json
orchestrator\.gitignore                       (project-root template)
orchestrator\Test-ArtifactValidator.ps1
orchestrator\Test-ClaudeRunner.ps1
orchestrator\Test-ConfigValidator.ps1
orchestrator\Test-Detector.ps1
orchestrator\Test-Integration.ps1
orchestrator\Test-Lockfile.ps1
orchestrator\Test-PathValidation.ps1
orchestrator\Test-Redactor.ps1
orchestrator\lib\ArtifactValidator.ps1
orchestrator\lib\Checkpoint.ps1
orchestrator\lib\ClaudeRunner.ps1
orchestrator\lib\Collector.ps1
orchestrator\lib\CompletionVerifier.ps1
orchestrator\lib\ConfigValidator.ps1
orchestrator\lib\Detector.ps1
orchestrator\lib\EnvSanitize.ps1
orchestrator\lib\Lockfile.ps1
orchestrator\lib\Packager.ps1
orchestrator\lib\PathValidation.ps1
orchestrator\lib\Preflight.ps1
orchestrator\lib\Redactor.ps1
orchestrator\lib\Signals.ps1
orchestrator\lib\State.ps1
orchestrator\tests\fake-claude.ps1
```

Tests are flat under `orchestrator\Test-*.ps1` (kept convention).

## Config fields found (sensitive values omitted)

`orchestrator.config.json` (top level only):

- projectRoot: matches C-drive root.
- executionMode: print
- claudeCommand: claude
- model: opus
- maxTurns: 120
- permissionMode: default
- safeMode: false
- allowBash: true
- allowedTools: Read, Write, Edit, Glob, Grep, Bash
- disallowedTools: (empty)
- dangerouslySkipPermissions: true
- sanitizeEnvironment: true
- envDenylist: null (uses runner default)
- envAllowList: (empty)
- killOnInteractivePrompt: true
- runnerPollSeconds: 3
- runTimeoutMinutes: 360
- llmPackageMaxChars: 200000
- requireExitMarker: false
- requireReport: false
- exitMarker: `##WAGGLE_RUN_COMPLETE##`

Sensitive observation: `dangerouslySkipPermissions=true` and `allowBash=true`
are both ENABLED. They are intentional for Phase 1.6 smoke, but they widen
the trust boundary the Phase 2A-2 review runner will sit on top of.

## Latest smoke iteration status

Path: `iterations\2026-05-06_15-49-51\`

state.json final status: `COMPLETED`
verdict.reason: exit 0 + valid completion signal + artifact validation passed
runner exit_code: 0
elapsed_seconds: 24.33
env_stripped: GMAIL_APP_PASSWORD (parent env secret correctly removed)
sanitize_environment: true

Files present in iteration folder:

- `prompt.md` (smoke prompt + appendix)
- `signals\claude_started.json`
- `signals\claude_completed.json`
- `claude_stdout.txt`, `claude_stderr.txt`, `claude_debug.log`
- `transcript_full.log`, `powershell_tail.txt`
- `git_metadata.json`, `run_metadata.json`
- `state.json`, `state.json.bak`
- `llm_input_package.md`, `llm_input_package_full.md`
- `redaction_report.json`

## Completion artifact status

`signals\claude_completed.json` was written, schema correct, `iteration_id`
matches folder. `completed_at` is `2026-05-06T15:49:51Z` (close to start, no
window violation).

## Stdout marker presence

Not strictly required (`requireExitMarker=false`) and the verifier did not
report a missing-marker downgrade. Smoke run reached `COMPLETED`.

## Root-level hello-from-orchestrator.txt

EXISTS at project root. Single line: "Tama on WaggleDance-orkestraattorin
smoke test". This is the failure surface P3 has to close: any future smoke
run can falsely pass artifact validation merely because this stale file
already exists, since there is no unique-per-iteration artifact.

## Stale artifact risk assessment

HIGH for the smoke flow specifically:

- The smoke prompt asks Claude to write the same fixed-path file every run
  (`hello-from-orchestrator.txt`).
- The current `ArtifactValidator` only checks the orchestrator's own files
  (state.json, run_metadata.json, claude_stdout.txt, llm_input_package.md,
  redaction_report.json). It does NOT verify Claude's own output artifact.
- A run where Claude does nothing but writes nothing wrong could still be
  marked COMPLETED if exit was 0 and signals were written.
- `requireExitMarker=false` further weakens the guard.

P3 closes this by injecting an iteration-id-bearing artifact path and
making validator fail unless that exact unique path exists with exact
content.

## Redaction false-positive observation

`redaction_report.json` reports `AWS_SECRET_KEY: 1`. Inspection of
`Redactor.ps1` line 28:

```
@{ name = 'AWS_SECRET_KEY'; pattern = '(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])' }
```

This matches ANY 40-character base64-ish boundary-isolated token. A normal
40-hex git commit SHA (`[0-9a-f]{40}`) is a strict subset of that character
class and is therefore swept up. In the package, `git_metadata.commit` was
rewritten to `[REDACTED:AWS_SECRET_KEY]`, hiding completely benign metadata.

Real AWS secret access keys are 40 chars from `[A-Za-z0-9/+=]`, so we cannot
shrink the class without losing detection. P2 will add CONTEXTUAL allowlists
for known SHA fields (commit, headRefOid, mergeCommit.oid, tag target,
"sha:", "oid:") rather than weaken the class.

## gitignore-sensitive warning origin

Preflight flagged the following as sensitive-but-untracked:

- `iterations\sample\state.json`
- `state\current.json`
- `orchestrator.config.json`

`git check-ignore` confirms NONE of those paths are currently ignored:

```
> git check-ignore iterations/sample/state.json   -> exit 1 (not ignored)
> git check-ignore state/current.json             -> exit 1 (not ignored)
> git check-ignore orchestrator.config.json       -> exit 1 (not ignored)
> git check-ignore hello-from-orchestrator.txt    -> exit 1 (not ignored)
> git check-ignore prompts/smoke.md               -> exit 1 (not ignored)
> git check-ignore orchestrator.config.phase1_6.bak.json -> exit 1
```

Root `.gitignore` covers Python/runtime patterns but does NOT cover any of
these orchestrator outputs. The orchestrator ships `orchestrator/.gitignore`
as a *template* with a clear comment ("place this file at your project root,
alongside the orchestrator/ folder"), but that template was never copied to
the project root.

`.git/info/exclude` does NOT exist at all
(`Glob .git/info/* -> No files found`).

P1 will use `.git/info/exclude` (local-only, not checked in) so the public
`.gitignore` is left alone. This satisfies the rule "prefer .git/info/exclude
over repository .gitignore for local orchestrator outputs/config/state".

## Parent env secret stripping observation

`run_metadata.json.env_stripped = ['GMAIL_APP_PASSWORD']` and
`sanitize_environment = true`. Parent secrets are being stripped before the
child claude process is launched, as required by global rule 16 and the
preflight policy. Continue this in P5.

## Dangerous settings present

- `dangerouslySkipPermissions: true` — Claude runs without the per-tool
  permission UI.
- `allowBash: true` — Bash is in `allowedTools`.

These are intentional for the current smoke flow but raise the bar for the
review runner Phase 2A-2 will add. PHASE2A2_HANDOFF will recommend a safer
review-mode tool profile.

## Recommendations for P1-P5

- P1: write `.git/info/exclude` covering iterations/, state/,
  orchestrator.config.json, orchestrator.config.*.bak.json, prompts/smoke.md,
  prompts/phase2a1_hardening.md, hello-from-orchestrator.txt, .claude/,
  *.pid, *.tmp, *.log.local. Verify with `git check-ignore`.
- P2: add a contextual allowlist to `Invoke-WaggleRedaction` so 40-hex SHAs
  in known git fields are preserved. Cover commit, sha, oid, headRefOid,
  mergeCommit.oid, targetCommitish, tag target. Do NOT relax the underlying
  AWS_SECRET_KEY class. Add a `Test-Redaction.ps1` (flat layout) covering
  preserve + redact cases.
- P3: extend the orchestrator prompt appendix to inject a unique
  per-iteration artifact path and required body containing the iteration_id.
  Extend `Test-IterationArtifacts` (or add a new helper invoked from
  `Resolve-PrintModeVerdict`) to verify exact path, exact content, freshness,
  size bound, no-NUL, UTF-8. Add `Test-SmokeValidation.ps1` (flat layout)
  with negative + positive cases. Update `prompts/smoke.md` to delegate the
  unique-artifact requirement to the orchestrator (high-level intent only).
- P4: add `orchestrator\Test-Syntax.ps1` using the parser API
  `[System.Management.Automation.Language.Parser]::ParseFile(...)` and run
  it against every `.ps1`/`.psm1` under `orchestrator\`.
- P5: run hardened smoke 3 times, distinct iteration_ids, distinct unique
  artifact paths, all COMPLETED, parent env stripped, no Bash needed by
  Claude.

## What this iteration intentionally does NOT do

- Does not implement `Invoke-WaggleReview.ps1`. That is Phase 2A-2.
- Does not push, PR, tag, or release.
- Does not modify product release tags or product source.
- Does not stage or commit.
- Does not print credentials or run `gh auth token` / credential helpers.
- Does not run browser automation.
