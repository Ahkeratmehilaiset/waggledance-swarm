# P3 — Real Claude smoke from clean session

## Command

```
powershell -NoProfile -ExecutionPolicy Bypass `
  -File ".\orchestrator\Invoke-WaggleIteration.ps1" `
  -ConfigPath ".\orchestrator.config.json" `
  -PromptFile ".\prompts\smoke.md"
```

Run from a clean shell, NOT nested inside the orchestrator (per master
prompt — Phase 2A-1 had to fall back to fake-claude due to lock
contention).

## Result

- Final status: **COMPLETED**
- Verifier reason: `exit 0 + valid completion signal + artifact validation passed`
- AUTO-PROCEED status: yes, safe to use as Phase 2A-2 review baseline

## Iteration

- iteration_id: `2026-05-06_19-45-54`
- folder: `iterations/2026-05-06_19-45-54/`
- pinned in: `docs/runs/orchestrator_phase2a2_review_runner_2026_05_06/baseline_iteration_id.txt`

## Required artifacts present

| Requirement | Path | Result |
|---|---|---|
| Unique smoke artifact (Phase 2A-1 P3 contract) | `iterations/2026-05-06_19-45-54/artifacts/smoke_2026-05-06_19-45-54.txt` | Present, 65 bytes |
| Artifact content matches iteration ID | "WaggleDance smoke artifact for iteration 2026-05-06_19-45-54" | Match (no trailing newline) |
| `redaction_report.json` | `iterations/2026-05-06_19-45-54/redaction_report.json` | Present |
| `llm_input_package.md` (review input) | `iterations/2026-05-06_19-45-54/llm_input_package.md` | Present |
| Completion signal | `iterations/2026-05-06_19-45-54/signals/claude_completed.json` | Present |
| Started signal | `iterations/2026-05-06_19-45-54/signals/claude_started.json` | Present |
| State pointer | `state/current.json` -> `2026-05-06_19-45-54` (status COMPLETED) | OK |
| No stale-root artifact contaminating result | none under repo root with this iteration ID | OK |

## Preflight notes

- Claude CLI: `claude.ps1` v2.1.126
- Auth: `claude.ai` first-party (max subscription) — NO token printed in
  the orchestrator output. The auth status JSON the preflight emitted
  contains `loggedIn`, `email`, `orgId`, `orgName`, `subscriptionType` —
  none of those are credentials/tokens. (It does include `email`. The
  email is a pre-existing user identifier and is not a secret in the
  Phase 2A-1 redactor's threat model.)
- `parent_env_secrets` warning was emitted for `GMAIL_APP_PASSWORD` —
  the orchestrator strips it via `sanitizeEnvironment=true`. The
  variable name was named in the preflight; its value was never
  printed.
- Lock state at start: `lock_free` (no concurrent orchestrator).
- Config used: live `orchestrator.config.json`
  (`dangerouslySkipPermissions=true`, `allowBash=true` — explicitly the
  "production" smoke config; review mode will NOT inherit these).

## No token printed

A grep over the live console output of this run shows no `gho_`,
`ghp_`, `github_pat_`, `Bearer `, `password=`, `PRIVATE KEY`, or
`GMAIL_APP_PASSWORD=<value>` patterns. The orchestrator's redactor
already enforces this on the persisted package.

## Conclusion

Phase 2A-1 fake-claude limitation is now closed. The unique-artifact
contract is honored end-to-end on the real CLI path. Baseline iteration
`2026-05-06_19-45-54` is what P9 will run architect / security /
reliability reviews over.
