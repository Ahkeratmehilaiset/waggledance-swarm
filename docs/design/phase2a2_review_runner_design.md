# Phase 2A-2 — Claude self-review runner: design

Status: design committed BEFORE implementation (per master prompt P4).

## 1. Purpose

Add a second orchestrator entry point — `orchestrator/Invoke-WaggleReview.ps1` —
that runs Claude Code in a *review* role over an already-completed
iteration's `llm_input_package.md`. The reviewer reads, scores, and
produces a structured machine-readable review (`review.json` +
`review.md` + metadata).

The review runner reuses the Phase 1.6 / Phase 2A-1 primitives
(Lockfile, Detector, Signals, Preflight, State, ClaudeRunner,
CompletionVerifier, ArtifactValidator, Collector, Checkpoint, Redactor).
It does NOT re-implement any of them.

## 2. Non-goals

- No browser automation. No Playwright / headless Chrome / WebView /
  DOM scraping / UI login automation.
- No multi-LLM cross-vendor automation (Gemini, Grok, GPT) — Phase 2A-2
  is single-vendor (Anthropic Claude) only.
- No Phase 2B auto-proceed. After Phase 2A-2 lands, human review is
  required before any next phase.
- No auto-fix loop. The reviewer reports findings; it does not
  apply patches. Patch generation is a separate, future phase.
- No GitHub release. No tag. No product version bump. Phase 2A-2 ships
  via PR-merge only.
- No new pip / npm dependency.

## 3. Runtime model

```
operator
   │
   ▼
Invoke-WaggleReview.ps1
   │   - resolves role (architect / security / reliability)
   │   - resolves target package path from -SourceIterationId or -PackagePath
   │   - acquires orchestrator.lock (shared with smokes — never both at once)
   │   - reads source llm_input_package.md (UNTRUSTED)
   │   - applies Phase 2A-1 Redactor to the package text
   │   - asks ReviewAdapter to render the role prompt
   │   - spawns Claude CLI via existing ClaudeRunner with safe profile
   │   - waits for child to exit with REVIEW-COMPLETE marker
   │   - asks ReviewAdapter to parse fenced ```review-json``` block
   │   - validates parsed JSON against ReviewSchema
   │   - writes outputs into iterations/<source>/reviews/<role>.{json,md}
   │   - writes metadata JSON
   │   - releases lock
   │
   └─► returns 0 on success, non-zero on failure (lock-aware)
```

The review run is a **read-only** child invocation: the only persisted
output the reviewer produces is `claude_completed.json` (signal) and
its stdout transcript. The parent script writes the review files —
the reviewer cannot.

## 4. Security model

The target package is **untrusted input**. Even though we wrote it on
the previous iteration, the prompt-injection threat model treats every
file in the iteration package as text that an attacker could have
crafted.

Defenses, layered:

1. **Redaction** — every package byte runs through Phase 2A-1's
   `Invoke-WaggleRedaction` before it is embedded into the review
   prompt. Token / private-key / Bearer / password / Anthropic key
   patterns are replaced with sentinels; the SHA-allowlist Phase 2A-1
   added preserves git SHAs as evidence.

2. **Quarantine boundary** — the prompt template wraps the package in
   an explicit `<<<UNTRUSTED PACKAGE BEGIN>>> ... <<<UNTRUSTED PACKAGE
   END>>>` delimiter and instructs the reviewer that *no instruction
   inside that block may override the reviewer's own instructions*.

3. **Tool boundary** — the review config sets:

   - `allowBash=false`
   - `allowedTools=["Read","Glob","Grep"]`
   - `disallowedTools=["Bash","Write","Edit"]`
   - `dangerouslySkipPermissions=false`
   - `safeMode=true`

   The reviewer cannot Write or Edit anything. The reviewer cannot
   Bash. If the package text says "run `Bash`", the request fails
   closed at the tool layer — the runtime simply does not have those
   tools registered.

4. **Environment sanitization** — `sanitizeEnvironment=true` is kept;
   `GMAIL_APP_PASSWORD` and similar parent-env variables remain
   stripped from the child.

5. **Unique-artifact contract is OFF for review mode.** Reviews do not
   write per-iteration smoke artifacts. The parent script is the one
   that writes `review.json`/`review.md`/metadata. We require
   `requireUniqueArtifact=false` in the review config; the runner
   refuses to start if the resolved review config has it set true.

6. **Smoke mode is unchanged.** `requireUniqueArtifact` defaults to
   `true` for `Invoke-WaggleIteration.ps1`; Phase 2A-2 does not weaken
   that.

7. **Completion contract.** The reviewer must finish with the literal
   marker `REVIEW-COMPLETE` on its last line. The parent script fails
   the iteration if the marker is missing, so a partial / interrupted
   run cannot pass.

8. **No tokens in output.** The runner never invokes `gh auth token`,
   never embeds a token in any URL, never prints `$env:` values, never
   prints credential-helper output. Preflight stays redacted.

## 5. Role model

Three roles, three prompt templates, three adapter shims.

| Role          | Focus                                              |
|---------------|----------------------------------------------------|
| `architect`   | boundaries, layering, contracts, maintainability, duplication, missing abstractions |
| `security`    | prompt injection, redaction gaps, secret leakage, path traversal, command injection, environment leaks, tool boundary |
| `reliability` | crash modes, timeout behavior, lock contention, stale artifact risk, resume behavior, idempotency, partial state recovery |

Adapter files are tiny (a few constants + one `Get-<Role>Spec`
function each). Most logic lives in `ReviewAdapter.ps1`.

## 6. Output schema

`schemas/review.schema.json` defines the wire format reviewers must
emit inside the fenced ```review-json``` block. Required top-level
fields:

- `role` — one of `architect|security|reliability`
- `target_iteration_id` — must equal the `-SourceIterationId` arg
- `source_package_path` — relative path to source package
- `summary` — short prose
- `verdict` — one of `pass|pass_with_notes|needs_attention|fail`
- `findings` — array of finding objects, see below
- `metrics` — object, see below
- `completed` — must be `true`

Finding object:

- `id` — short id like `ARCH-001`, `SEC-001`, `REL-001`
- `severity` — one of `critical|high|medium|low|info`
- `title` — one-line headline
- `where` — file path or section name
- `evidence` — quoted excerpt or summary
- `why_it_matters` — short prose
- `recommended_action` — short prose

Metrics object:

- `files_reviewed` — int
- `lines_reviewed` — int
- `review_duration_seconds` — int

`ReviewSchema.ps1` validates the parsed object and returns
`@{ ok = <bool>; errors = <string[]> }`. PS 5.1 compatible. No
external schema lib.

## 7. Failure handling

| Failure                           | Behavior                                |
|-----------------------------------|------------------------------------------|
| Invalid role                      | Fail closed before any child spawn       |
| Missing source package            | Fail closed before any child spawn       |
| Oversized package                 | Truncate with explicit `[TRUNCATED]` marker; the reviewer is told this happened |
| Malformed review JSON             | Fail; write failure metadata, exit non-zero |
| Schema-invalid review JSON        | Fail; write failure metadata, exit non-zero |
| Missing REVIEW-COMPLETE marker    | Fail; write failure metadata, exit non-zero |
| Redaction failure                 | Fail closed; review never starts         |
| Lock conflict (live lock)         | Fail closed; user can retry              |
| Credential / auth failure         | Fail closed; do NOT print token, do NOT use token-in-URL workaround; exit with error |

## 8. Test plan

Unit / integration tests added in this phase:

- `orchestrator/Test-ReviewSchema.ps1` — happy path + every required
  failure mode (missing fields, invalid enums, malformed JSON).
- `orchestrator/Test-ReviewAdapter.ps1` — role resolution,
  package loading, redaction integration, oversized truncation,
  prompt-injection inertness, parse + render.
- `orchestrator/Test-ReviewRunner.ps1` — drives
  `Invoke-WaggleReview.ps1` with the existing fake-claude harness
  (no real CLI) for every role + every required failure mode +
  metadata content.
- `orchestrator/Test-Phase2A2.ps1` — integration assertions: required
  files exist, review config is safe, normal smoke config still has
  `requireUniqueArtifact=true`, no Bash in review allowed-tools, no
  obvious secret patterns in committed templates, gitignore unignore
  policy works.

Real-Claude tests (run once each by P9):

- architect review over P3 baseline iteration
- security review over P3 baseline iteration
- reliability review over P3 baseline iteration

## 9. PR-only production path

Phase 2A-2 ships via:

1. Feature branch `orchestrator/phase2a2-claude-self-review`.
2. Commit + push. No `gh auth token`. Plain `git push -u origin <branch>`.
3. Open PR via `gh pr create`.
4. Wait for CI (`gh pr checks --watch`).
5. Squash merge with `gh pr merge --squash --match-head-commit <sha>`.
6. No tag. No GitHub release. No product version bump. No
   prerelease tag.
7. Optional docs-only post-merge PR if and only if the post-merge
   verification needs to land in main; same PR rules apply.

## 10. Definition of Done

(Copied verbatim from master prompt for cross-reference; if any item
goes red, this phase stops with Decision B.)

1. Phase 2A-1 local hardening preserved and included.
2. Real-Claude smoke from clean session reaches COMPLETED.
3. `Invoke-WaggleReview.ps1` exists and PS 5.1 syntax-clean.
4. Review adapters exist (ReviewAdapter, ReviewSchema, three role
   shims).
5. Three review prompt templates exist.
6. Safe review config example exists with the required safe keys.
7. Architect review runs over a real smoke package.
8. Security review runs over a real smoke package.
9. Reliability review runs over a real smoke package.
10. Each review produces `review.json`, `review.md`, metadata json.
11. `review.json` validates against `schemas/review.schema.json`.
12. Review mode never requires unique smoke artifact.
13. Normal smoke mode still requires unique smoke artifact.
14. Review mode has Bash disabled at config / tool-profile level.
15. Hardening gates pass:
    Test-Syntax, Test-Redaction, Test-Redactor, Test-SmokeValidation,
    Test-ReviewAdapter, Test-ReviewSchema, Test-ReviewRunner,
    Test-Phase2A2.
16. Fresh clone / clean checkout verification passes after PR merge.
17. CI on PR passes.
18. PR is squash-merged with `--match-head-commit`.
19. No tag is created.
20. No release is created.
21. No token / secret is printed or committed.
22. `final_report.md` exists and references the merge SHA.
