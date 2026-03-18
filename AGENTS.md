# AGENTS.md

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
