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
- Reproduce failures before changing code.
- Prefer the smallest safe patch.
- Do not add dependencies unless explicitly required.
- Do not refactor unrelated files.
- For CI failures, prioritize deterministic fixes over retries/timeouts.
- Treat broken tests, unsafe write paths, silent fallbacks, and misleading docs as high priority.
- Always report exact commands run and the final test status.

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
