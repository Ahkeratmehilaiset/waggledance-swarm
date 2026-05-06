# P7 -- Real smoke + 3 real reviews on Phase 2A-3

## Real smoke

```
powershell -NoProfile -ExecutionPolicy Bypass `
  -File ".\orchestrator\Invoke-WaggleIteration.ps1" `
  -ConfigPath ".\orchestrator.config.json" `
  -PromptFile ".\prompts\smoke.md"
```

Result: COMPLETED. iteration_id = `2026-05-06_22-31-16`.

Pinned in `baseline_iteration_id.txt`.

## Real reviews

```
powershell -NoProfile -ExecutionPolicy Bypass `
  -File ".\orchestrator\Invoke-WaggleReview.ps1" `
  -ConfigPath ".\orchestrator.config.json" `
  -ReviewConfigPath ".\orchestrator.config.review.example.json" `
  -SourceIterationId "2026-05-06_22-31-16" `
  -Role architect    # then security, then reliability
```

| Role        | Status    | Verdict          | files_reviewed | lines_reviewed | findings |
|-------------|-----------|------------------|----------------|----------------|----------|
| architect   | COMPLETED | needs_attention  | **13**         | **1400**       | 6        |
| security    | COMPLETED | needs_attention  | **14**         | **1850**       | 9        |
| reliability | COMPLETED | pass_with_notes  | **14**         | **1450**       | 5        |

Compare to Phase 2A-2 reviews on the same shape of smoke package:

| Role        | Phase 2A-2 verdict | Phase 2A-2 files | Phase 2A-3 verdict | Phase 2A-3 files |
|-------------|--------------------|------------------|--------------------|--------------------|
| architect   | pass               | 0                | needs_attention    | 13                 |
| security    | pass_with_notes    | 0                | needs_attention    | 14                 |
| reliability | pass               | 0                | pass_with_notes    | 14                 |

**Empty review surface is resolved.** All three roles now have real
source evidence (orchestrator + lib + tests + schema + prompts via
the supplement) and produce verdicts that reflect the evidence.

## Supplement disclosure (Phase 2A-3 P4 rule)

Every reviewer's `summary` opens with explicit disclosure that the
evidence came from the supplement, not the target package:

- architect: "Evidence drawn from the review surface supplement; the
  target iteration's package contained only run/git metadata with
  empty raportti.md, transcript, stdout, and stderr sections..."
- security:  "Evidence drawn primarily from the review surface
  supplement; the target iteration's run/git metadata showed a
  write-mode smoke run with empty stdout/stderr/transcript and is
  not itself a finding..."
- reliability: "Evidence drawn from the review surface supplement,
  not from the target iteration's package -- the target package's
  raportti, PowerShell tail, Claude stdout, and Claude stderr
  sections were all empty..."

## SEC-001 false positive resolution

Phase 2A-2 security review raised:

> SEC-001 (low) Runner invoked with --dangerously-skip-permissions
> while allowing Bash
> where: run_metadata.json -> command_line

That was the target write-mode smoke iteration's metadata being
flagged as if it were review-mode metadata. Phase 2A-3 prompt P4
explicitly tells the reviewer to NOT do that.

Phase 2A-3 security review's behavior on the same metadata:

- The reviewer recorded `SEC-005` at **info** severity (not low,
  not a finding) with explicit reasoning:

  > "Per the prompt's explicit guidance, write-mode
  > `run_metadata.json` having Bash and
  > `--dangerously-skip-permissions` is NOT a security finding by
  > itself; flagging it would reproduce the Phase 2A-2 false-
  > positive SEC-001. Recording as `info` only so the reviewer's
  > reasoning is auditable."

The reviewer correctly applied the new prompt distinction.
**SEC-001 false positive is resolved**.

## Other findings (not false positives)

The Phase 2A-3 security reviewer DID raise a legitimate
observation:

- `SEC-001` (medium) about `Redactor.ps1`'s `COOKIE_HEADER` /
  `SET_COOKIE` regex patterns appearing damaged in the supplement.

  The reviewer correctly hypothesised this is "a packaging-time
  self-redaction artifact": the supplement runs `Invoke-WaggleRedaction`
  over its own source files, so when the redactor's source contains
  the literal string `cookie:` (inside its regex patterns), the
  cookie pattern matches and produces `[REDACTED:COOKIE_HEADER]`
  in the supplement view. The on-disk Redactor.ps1 is fine; the
  supplement's redacted view of it is misleading for that file
  specifically.

This is genuine review value -- exactly what we want from the
review runner. It is documented in the design doc's "Remaining
risks" section and noted in `final_report.md` for future hardening
(Phase 2A-4 candidate: skip self-redaction for files whose path
matches `orchestrator/lib/Redactor.ps1`).

## Review-mode safety profile (independently verified)

For every role, `<role>.metadata.json` records the safe profile:

| Field                          | Value         |
|--------------------------------|---------------|
| status                         | COMPLETED     |
| allow_bash                     | **false**     |
| dangerously_skip_permissions   | **false**     |
| require_unique_artifact        | **false**     |
| sanitize_environment           | true          |
| allowed_tools                  | Read,Glob,Grep |
| disallowed_tools               | Bash,Write,Edit |
| package_quality.sparse         | true (smoke is metadata-only) |
| package_quality.source_supplement_used | true  |
| package_quality.source_supplement_files | n=15 |

The runtime safety gate (P1.5 Assert-WaggleReviewSafeProfile) was
called on each invocation -- without it throwing, the subprocess
launch proceeded. (Test-ReviewSafety.ps1 verifies the gate throws
on corrupt profiles.)

## package_quality.json

Each review's `_staging_*/` dir holds a `package_quality.json`
recording reviewable_files_count, reviewable_lines_count,
source_section_count, sparse, sparse_reason, and the supplement
file list. (The merged review's `<role>.metadata.json` carries the
same record under `package_quality`.)

## No tokens

Pattern scan (`gho_`, `ghp_`, `github_pat_`, `Authorization:
Bearer`, `password=`, `PRIVATE KEY`, `GMAIL_APP_PASSWORD`,
`AWS_SECRET_ACCESS_KEY`) over the six review output files produces
zero hits. The supplement was redacted before embedding; the
reviewers' summaries / findings discuss redaction sentinels
(`[REDACTED:GH_TOKEN_*]`) only as evidence that redaction worked.

## Done

P7 PASS:

- Real smoke `2026-05-06_22-31-16` COMPLETED
- All 3 reviews COMPLETED
- `files_reviewed > 0` for every role (13, 14, 14)
- All 3 summaries explicitly disclose supplement use
- Phase 2A-2 SEC-001 false positive does NOT recur
- Review-mode safety profile invariants hold
- New legitimate findings exposed by the supplement (the value-
  add of the surface fix)
