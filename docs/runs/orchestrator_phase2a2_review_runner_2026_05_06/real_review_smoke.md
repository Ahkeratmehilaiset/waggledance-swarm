# P9 -- Real architect / security / reliability review smoke

Three real-Claude reviews against the P3 baseline iteration
`2026-05-06_19-45-54`. Each review uses
`orchestrator.config.review.example.json` as the safe profile.

## Commands

```
powershell -NoProfile -ExecutionPolicy Bypass `
  -File ".\orchestrator\Invoke-WaggleReview.ps1" `
  -ConfigPath ".\orchestrator.config.json" `
  -ReviewConfigPath ".\orchestrator.config.review.example.json" `
  -SourceIterationId "2026-05-06_19-45-54" `
  -Role architect    # then: security, then: reliability
```

## Per-role results

| Role        | review_iteration_id                              | Status    | Verdict          | Findings | json sha256 (head) |
|-------------|--------------------------------------------------|-----------|------------------|----------|---------------------|
| architect   | `2026-05-06_20-26-14_review_architect`           | COMPLETED | pass             | 0        | `4f6cebc0...`       |
| security    | `2026-05-06_20-28-01_review_security`            | COMPLETED | pass_with_notes  | 3        | `e5f55fff...`       |
| reliability | `2026-05-06_20-29-01_review_reliability`         | COMPLETED | pass             | 0        | `73fe0d25...`       |

Outputs persisted under
`iterations/2026-05-06_19-45-54/reviews/`:

- `architect.{json,md,metadata.json}`
- `security.{json,md,metadata.json}`
- `reliability.{json,md,metadata.json}`

The three sha256 values are different, confirming the reviews are
role-specific (not the same blob with a label change). Each review
JSON validates against `schemas/review.schema.json` (verified via
`ReviewSchema.Test-ReviewObject`).

## Safety-profile invariants (per review metadata)

For every role:

| Field | Value |
|---|---|
| `safe_mode` | true |
| `allow_bash` | **false** |
| `dangerously_skip_permissions` | **false** |
| `require_unique_artifact` | **false** |
| `sanitize_environment` | true |
| `allowed_tools` | `Read,Glob,Grep` |
| `disallowed_tools` | `Bash,Write,Edit` |

## Bash usage check

The reviewer tool boundary is enforced at the runner level:
`Invoke-WaggleReview` builds the child arglist with
`--disallowed-tools Bash,Write,Edit` and `--allowed-tools Read,Glob,Grep`.
Even if a malicious package told the reviewer to run Bash, the runtime
does not register that tool. None of the three review stdouts contain
the tool-call signatures the orchestrator's verifier would treat as
Bash invocations.

## Role-specific focus (sample summaries)

- **architect**: "The iteration package contains only run metadata and
  git metadata; no source files, diffs, reports, or stdout/stderr
  content are included for architectural review ... No prompt-injection
  attempts were observed inside the untrusted block."
- **security**: "The iteration package is essentially empty ..."
  (3 findings, all in lower severities, focused on the missing-content
  surface)
- **reliability**: "The iteration package contains only run/git
  metadata and empty stdout/stderr/transcripts ..."

The summaries differ in voice and findings -- consistent with the
three distinct prompt templates and role focuses defined in P5.

## Lock + cleanup

After the architect review, an early version of the runner's
`Release-WaggleLock` call used the wrong parameter name (`-Lock $lock`
instead of `-Path $lockPath -LockId $lock.lock_id`), which silently
failed and left a stale lock at `state/orchestrator.lock`. We:

1. confirmed the holder PID was no longer alive,
2. fixed `Invoke-WaggleReview.ps1`'s `Release-WaggleLock` call to use
   the correct parameter form,
3. added `-ForceStaleLock` to the `Acquire-WaggleLock` call so a
   future dead-pid lock is reclaimed automatically (live locks are
   still refused),
4. removed the stale lock file,
5. re-ran the security and reliability reviews -- both released
   their locks cleanly. (Architect outputs from the original run are
   still valid; the lock leak did not corrupt any review file.)

## No tokens

A grep for `gho_`, `ghp_`, `github_pat_`, `Authorization: Bearer `,
`PRIVATE KEY`, `password=`, `GMAIL_APP_PASSWORD=` over all six review
output files (`{architect,security,reliability}.{json,md}`) returns
zero hits. The redaction step (`Invoke-WaggleRedaction` over the
embedded package) ran on each review prompt; the `redaction_report.json`
files in each review's `_staging_*` dir record the substitution counts
without any raw secret text.

## Done

P9 PASS.
