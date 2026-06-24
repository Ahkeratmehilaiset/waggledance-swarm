# AGENTS.md

## Source-of-truth rules (added after the 2026-04-11 recovery incident)
- The persistent C-drive repo (`C:\Python\project2`) is the ONLY source of truth.
- Never develop in U:, RAM-disk, temp, or zip-extract folders. RAM-disks
  disappear at shutdown and take your uncommitted work with them.
- Every green checkpoint MUST be committed and pushed to GitHub immediately.
- Backups are SECONDARY. GitHub is the primary history.
- If your working tree has no `.git`, STOP. Do NOT `git init` a backup snapshot.
  Instead: clone the GitHub repo fresh into a persistent C-drive folder, then
  overlay the current working files on top of the clone so the real history
  is preserved. See `docs/RECOVERY_POLICY.md`.
- If work is reconstructed from reports (because the originals were lost),
  say so explicitly in the commit message — include the report paths and
  the known-good RC commit SHA the reconstruction targets.
- Use `tools/savepoint.ps1 -Message "..."` to checkpoint. It refuses to run
  off the C: drive, refuses to run from a RAM-disk, shows git status, runs
  the tests you pass, commits, and pushes in a single safe step.

## Task rules
- Operate in non-interactive mode by default. If the operator has already
  granted session-level approval, do not ask yes/no or 1/2/3 questions; choose
  the safest useful option and continue.
- Prefer autonomous defaults: approve low-risk continuation, choose the
  recommended/lowest-risk path when options are equivalent, and unblock the
  bridge queue with scoped work instead of waiting for operator relay.
- Only stop for operator input when a destructive action, credential/secret,
  external payment, unresolved write-scope conflict, or legally/security
  sensitive decision cannot be handled from repo evidence.
- Reproduce failures before changing code.
- Prefer the smallest safe patch.
- Do not add dependencies unless explicitly required.
- Do not refactor unrelated files.
- For CI failures, prioritize deterministic fixes over retries/timeouts.
- Treat broken tests, unsafe write paths, silent fallbacks, and misleading docs as high priority.
- Always report exact commands run and the final test status.
- Local test scope: CI is the authoritative full-suite gate and runs the whole
  suite at the exact head before any merge — do NOT re-run the full ~795-test
  suite locally on every head. For local iteration, run only the AFFECTED tests:
  `python tools/select_affected_tests.py --changed-from-git origin/main` (or
  `--files <changed paths>`) prints the affected test files, or `full_suite`
  when it cannot narrow safely. The selector is FAIL-SAFE — it returns the full
  suite whenever the affected set is uncertain (a broad-impact file such as
  conftest/pyproject/charter/`__init__`, a changed source mapping to no test, an
  unknown file type, or empty input), so it never silently under-runs. Run the
  full suite locally only when the selector says `full_suite`. Still report the
  final test status of whatever you ran.

## Runtime audit rules
- Prefer runtime evidence over static guesses.
- Reproduce before concluding.
- Do not edit source files during audit-only runs.
- Write logs and scratch files only under .codex-audit/.
- Separate findings into:
  - confirmed_bug
  - suspected_bug
  - improvement
- For each finding include:
  - evidence
  - reproduction steps
  - likely root cause
  - smallest safe fix suggestion
- Flag silent fallbacks, swallowed exceptions, flaky tests, and docs-vs-behavior mismatches.
