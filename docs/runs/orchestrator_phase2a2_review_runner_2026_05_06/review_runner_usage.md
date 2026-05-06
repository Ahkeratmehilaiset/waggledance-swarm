# P10 -- Review runner usage

## What it is

`orchestrator/Invoke-WaggleReview.ps1` is a Phase 2A-2 entry point that
runs Claude Code in a *review* role over an already-completed
iteration's `llm_input_package.md`. It is a peer of
`orchestrator/Invoke-WaggleIteration.ps1`, not a replacement.

## Quickstart

Architect review:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File ".\orchestrator\Invoke-WaggleReview.ps1" `
  -ConfigPath ".\orchestrator.config.json" `
  -ReviewConfigPath ".\orchestrator.config.review.example.json" `
  -SourceIterationId "<iteration_id>" `
  -Role architect
```

Security review:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File ".\orchestrator\Invoke-WaggleReview.ps1" `
  -ConfigPath ".\orchestrator.config.json" `
  -ReviewConfigPath ".\orchestrator.config.review.example.json" `
  -SourceIterationId "<iteration_id>" `
  -Role security
```

Reliability review:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File ".\orchestrator\Invoke-WaggleReview.ps1" `
  -ConfigPath ".\orchestrator.config.json" `
  -ReviewConfigPath ".\orchestrator.config.review.example.json" `
  -SourceIterationId "<iteration_id>" `
  -Role reliability
```

## Outputs

For a review of iteration `<id>` and role `<role>`, outputs land at:

```
iterations/<id>/reviews/<role>.json          # schema-validated review
iterations/<id>/reviews/<role>.md            # rendered markdown
iterations/<id>/reviews/<role>.metadata.json # safety profile, sha256s, run_result
iterations/<id>/reviews/_staging_<rev_id>/   # per-run staging:
                                             #   review_prompt.md
                                             #   review_stdout.txt / review_stderr.txt
                                             #   review_debug.log
                                             #   redaction_report.json
```

The schema is `schemas/review.schema.json` (Phase 2A-2). The runner
fails closed if the reviewer's output is not parseable, schema-invalid,
or missing the `REVIEW-COMPLETE` marker.

## Review config safety profile

`orchestrator.config.review.example.json` is a **safe** profile. The
runner additionally enforces these invariants in code (`Resolve-Wagg-
leReviewEffectiveProfile` hard-clamps them and rejects the run if they
are violated):

| Key | Required value | Why |
|---|---|---|
| `safeMode` | `true` | trust-boundary baseline |
| `allowBash` | `false` | review is read-only; no shell |
| `dangerouslySkipPermissions` | `false` | review must not bypass Claude Code's permission prompts |
| `requireUniqueArtifact` | `false` | review writes nothing under the iteration except its own output files (parent script writes them) |
| `sanitizeEnvironment` | `true` | env-secrets stripped via Phase 1.6 EnvSanitize |
| `allowedTools` | `["Read", "Glob", "Grep"]` | reviewer may inspect, not mutate |
| `disallowedTools` | `["Bash", "Write", "Edit"]` | belt-and-braces deny list |
| `requireExitMarker` | `true` | run is failed if `REVIEW-COMPLETE` is missing |
| `exitMarker` | `REVIEW-COMPLETE` | the marker the reviewer prints |

## Why Bash is disabled

The reviewer reads an *untrusted* package -- the orchestrator embeds
the previous iteration's `llm_input_package.md` between
`<<<UNTRUSTED PACKAGE BEGIN>>>` / `<<<UNTRUSTED PACKAGE END>>>`
delimiters. If a prompt-injection attempt inside that package
convinces the reviewer to run `Bash`, the consequences range from
"reads local secrets" to "writes to repo". With Bash disabled at
both the config level and the runtime tool-profile level, the
attempt fails before the reviewer can do anything dangerous.

## Why unique-artifact is disabled for review mode

The Phase 2A-1 unique-artifact contract (`requireUniqueArtifact=true`)
is a smoke-mode invariant: the smoke prompt asks Claude to *write* a
specific file, and the orchestrator validates it exists with the
right content. Review mode is read-only -- the reviewer must NOT
write any file. The parent script (`Invoke-WaggleReview`) is the one
that writes `review.json` / `review.md` / metadata. So the unique-
artifact check is irrelevant for reviews; turning it on would force
the reviewer to violate its own read-only contract.

## Why normal smoke STILL requires unique artifact

The smoke flow is unchanged. `orchestrator/Invoke-WaggleIteration.ps1`
defaults `requireUniqueArtifact` to `true`; the live
`orchestrator.config.json` does not set it explicitly, so it stays
`true`. Phase 2A-2 added `Test-Phase2A2.ps1` that asserts both:

- the live smoke flow's prompt-appendix injects the SMOKE ARTIFACT
  CONTRACT block (only happens when `requireUniqueArtifact=true`),
- review-mode's safe profile sets it `false`.

If a future change weakens either invariant, `Test-Phase2A2` fails.

## Interpreting the verdicts

- `pass` -- no findings above `info` severity.
- `pass_with_notes` -- only `low` / `info` findings.
- `needs_attention` -- at least one `medium` or `high` finding.
- `fail` -- at least one `critical` finding.

A `critical` finding always means a real, exploitable, or
data-destroying problem. A schema-invalid review or missing
`REVIEW-COMPLETE` marker is a **runner failure**, not a `fail`
verdict -- the runner exits non-zero and the metadata records the
errors.

## Known limitations

1. **Single-vendor.** Phase 2A-2 supports Anthropic Claude only. No
   Gemini / GPT / Grok lane.
2. **No auto-fix.** The reviewer reports findings; it does not apply
   patches.
3. **No multi-PASS runs.** Each invocation is one role over one
   iteration. To get all three views, run three times.
4. **Async stdout in `ClaudeRunner`.** The smoke flow's runner uses
   `BeginOutputReadLine` + `Register-ObjectEvent`, which on PS 5.1
   loses stdout for fast-exit children (typically only matters for
   tests). The review runner uses a synchronous subprocess wrapper
   (`Invoke-WaggleReviewSubprocess`) to avoid that, so review-mode
   captures stdout reliably.
5. **Lock contention.** Reviews and smokes share the
   `state/orchestrator.lock` -- you cannot run a smoke and a review
   at the same time. Stale dead-pid locks are reclaimed
   automatically; live locks are refused.
6. **PR-only landing.** Phase 2A-2 ships via PR merge; no tag, no
   release.

## Phase 2B

Phase 2B is **not** auto-started by Phase 2A-2. After the PR merges,
human review is required before any next-phase decision (e.g.,
multi-LLM, auto-fix loop, or Phase 2B's actual scope).
