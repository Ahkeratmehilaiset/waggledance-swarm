# Phase 2A-1 — smoke hardening (P3)

## Goal

A smoke run must not pass merely because a stale artifact from any
previous iteration happens to be present. The Phase 1.6 smoke flow
verified `hello-from-orchestrator.txt` at the project root with no
iteration_id linkage, so any old run could trivially make a new run
appear green.

## Implementation summary

### 1. `prompts/smoke.md` (high-level intent only)

Rewritten to delegate the artifact path/content to the orchestrator
appendix. The user-facing prompt no longer hardcodes a path or body. It
explicitly tells Claude to read the SMOKE ARTIFACT CONTRACT block that
the orchestrator appends and to follow that, using only the Write tool
(no shell), modifying no other files.

### 2. `orchestrator\Invoke-WaggleIteration.ps1` (orchestrator-side appendix)

The orchestrator now computes:

```
$smokeArtifactRel  = "iterations/<iteration_id>/artifacts/smoke_<iteration_id>.txt"
$smokeArtifactAbs  = <iterationFolder>\artifacts\smoke_<iteration_id>.txt
$smokeArtifactBody = "WaggleDance smoke artifact for iteration <iteration_id>"
```

Both the path and the body carry the iteration_id, so any leftover file
from a prior iteration cannot satisfy the contract. A new appendix block
("SMOKE ARTIFACT CONTRACT (Phase 2A-1 P3, unique per iteration)") is
emitted into the per-iteration `prompt.md` BEFORE the existing WAGGLE
COMPLETION CONTRACT block. The completion contract is unchanged.

A new optional config field `requireUniqueArtifact` (default `true`)
controls whether this contract is enforced. Phase 2A-2's review runner
can set it to `false` to skip the smoke contract for review prompts.

### 3. `orchestrator\lib\ArtifactValidator.ps1` (validator)

New function `Test-UniqueIterationArtifact` checks:

- file exists at exact absolute path;
- size between MinBytes (default 1) and MaxBytes (default 4096);
- mtime >= run start (1-second skew tolerance);
- no NUL bytes anywhere in the file;
- strict UTF-8 (decoder throws on invalid sequences);
- content equals expected body modulo a single trailing CRLF / LF / CR
  (Write tool may append a newline; we tolerate exactly one).

The function returns `{ ok; checks; errors }`; errors are descriptive
strings not raw secrets.

### 4. `orchestrator\lib\CompletionVerifier.ps1` (verdict wiring)

`Resolve-PrintModeVerdict` gained three optional parameters:
`UniqueArtifactPath`, `UniqueArtifactBody`, `UniqueArtifactMaxBytes`.
When `UniqueArtifactPath` is non-empty, the verifier calls
`Test-UniqueIterationArtifact` after the existing artifact validation
gate. Failure downgrades the verdict to `COMPLETED_UNVERIFIED` with a
descriptive reason; everything else is unchanged.

### 5. `orchestrator\Invoke-WaggleIteration.ps1` (verdict call site)

`Resolve-PrintModeVerdict` is now called via a parameter splat that
conditionally includes the unique-artifact parameters only when
`requireUniqueArtifact` is true. This keeps the review-runner path
clean.

## Exact validation rules

The unique artifact must:

1. exist at `<iterationFolder>\artifacts\smoke_<iteration_id>.txt`;
2. be 1..4096 bytes;
3. have mtime within run-start - 1s ... now;
4. contain no NUL byte;
5. decode as strict UTF-8 (no fallback bytes);
6. equal the expected body string `WaggleDance smoke artifact for iteration <iteration_id>` (after stripping a single trailing newline).

A stale `hello-from-orchestrator.txt` at the project root, or any earlier
iteration's `smoke_*.txt` under a different iteration folder, cannot
satisfy these rules because the path and body both embed the new
iteration_id.

## Negative tests result

`orchestrator\Test-SmokeValidation.ps1` (flat layout, no `tests\` subdir):

```
PASS  NEG: missing new artifact -> ok=false
PASS  NEG: missing new artifact -> reports unique_artifact_present false
PASS  NEG: stale-only artifact at old path cannot satisfy new path
PASS  NEG: wrong content -> ok=false
PASS  NEG: wrong content -> content_exact failure
PASS  NEG: stale mtime -> ok=false
PASS  NEG: stale mtime -> reports unique_artifact_fresh
PASS  NEG: file too large -> ok=false
PASS  NEG: file too large -> reports size_max
PASS  NEG: NUL byte -> ok=false
PASS  NEG: NUL byte -> reports no_nul
PASS  POS: exact path + exact content + fresh mtime -> ok=true
PASS  POS: zero errors
PASS  POS: single trailing LF tolerated

Result: 14/14 tests passed
```

The negative tests demonstrate, in code:

- A new iteration cannot pass when its artifact is missing.
- A new iteration cannot pass when only an old iteration's artifact
  exists at a different path (the stale-artifact attack).
- Wrong content fails even at the right path.
- Stale mtime fails even with the right content at the right path.
- Oversized writes fail.
- NUL-byte payloads fail.

The positive tests demonstrate that the exact contract DOES pass and
that a single trailing LF (which `Write-File` style tools commonly emit)
is tolerated.

## Stale artifact risk

Closed for the smoke flow. The orchestrator-injected contract makes the
artifact unique per iteration, and the validator rejects stale, wrong
or large or non-UTF-8 files.

The Phase 1.6 smoke surface (root `hello-from-orchestrator.txt`) is now
irrelevant: it is at a different path, has different content, and is no
longer requested. P1 added it to `.git/info/exclude`.

## Stdout marker not relied upon

`requireExitMarker=false` remains the configured default; the unique
artifact is the strong identity check. If a future config flips
`requireExitMarker=true` the verifier already enforces it before the
unique-artifact pass; both gates compose.

## Bash not required

The contract instructs Claude to use the Write tool only and explicitly
forbids shell. P5 will verify that the smoke prompt completes without
Bash tool use even though `allowBash=true` in the current config.

## Known limitations

- The contract is a soft instruction to Claude; nothing prevents Claude
  from using Bash anyway. The validator itself does not inspect
  `claude_stdout.txt` for tool-call traces. If we later need a hard
  guarantee, removing Bash from `allowedTools` for the smoke flow is the
  cleanest fix; that is recommended in PHASE2A2_HANDOFF as a follow-up.
- The validator only inspects the unique smoke artifact; it does not yet
  check that other files were NOT touched. A "no extra writes" guard
  would require a pre-run snapshot diff and is out of scope here.
- Trailing-newline tolerance is exactly one LF / CR / CRLF. Files with
  two or more trailing blank lines fail content match. This is
  intentional — we want strict identity but not so strict that `Set-Content`
  defaults trip us.
