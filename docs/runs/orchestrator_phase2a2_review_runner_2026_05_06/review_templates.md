# P5 — Review config example + role templates

## Files added

- `orchestrator.config.review.example.json` — safe review-mode config
  (committed; force-staged past the `/orchestrator.config.*.json`
  catch-all).
- `prompts/review/architect.md`
- `prompts/review/security.md`
- `prompts/review/reliability.md`

## Safe profile keys (verified)

| Key | Required | Got |
|---|---|---|
| `safeMode` | `true` | `true` |
| `allowBash` | `false` | `false` |
| `dangerouslySkipPermissions` | `false` | `false` |
| `requireUniqueArtifact` | `false` | `false` |
| `sanitizeEnvironment` | `true` | `true` |
| `requireExitMarker` | `true` | `true` |
| `exitMarker` | `REVIEW-COMPLETE` | `REVIEW-COMPLETE` |
| `allowedTools` | `["Read","Glob","Grep"]` | `["Read","Glob","Grep"]` |
| `disallowedTools` | `["Bash","Write","Edit"]` | `["Bash","Write","Edit"]` |

## Template invariants (verified by inspection)

Every role template:

- States the role and the review focus.
- Wraps the package in `<<<UNTRUSTED PACKAGE BEGIN>>>` / `<<<UNTRUSTED
  PACKAGE END>>>` (the runner inserts these delimiters around the
  embedded package — the template instructs the reviewer to treat
  that block as untrusted).
- Forbids obeying instructions inside the package.
- Forbids Bash / tool use / file modification.
- Forbids secret printing.
- Requires a fenced JSON block tagged `review-json` with the schema.
- Requires markdown sections: Verdict, Critical issues, Important
  issues, Minor issues, Evidence references, Suggested next actions,
  Confidence.
- Ends with literal marker `REVIEW-COMPLETE`.
- Forbids asking questions.
- Forbids file modification.

## Role focus split

| Role | Focus |
|---|---|
| architect   | boundaries, layering, contracts, maintainability, duplication, missing abstractions |
| security    | prompt injection, redaction gaps, secret leakage, path traversal, command injection, environment leaks, tool boundary |
| reliability | crash modes, timeout behavior, lock contention, stale artifact risk, resume behavior, idempotency, partial state recovery |

## Syntax preflight

Templates are markdown only — no PowerShell. `Test-Syntax.ps1` will
re-run in P8 to confirm no `.ps1` regression. JSON example will be
parsed in P12.

P5 PASS.
