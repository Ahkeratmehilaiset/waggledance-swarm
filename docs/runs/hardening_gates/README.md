# `docs/runs/hardening_gates/` -- runtime gate reports

Local-only runtime artifacts produced by
`orchestrator/Run-WaggleHardeningGates.ps1` (Phase 2A-5 ARCH-006).

## What lands here

When you run

```
powershell -NoProfile -ExecutionPolicy Bypass -File .\orchestrator\Run-WaggleHardeningGates.ps1
```

without a `-ReportPath` argument, the driver writes:

- `<UTC-timestamp>.json` -- a JSON report for that run
  (e.g. `2026-05-07T00-30-15Z.json`)
- `latest.json` -- a `Copy-Item` of the most recent report so
  callers can find "the last run" without scanning the directory

Both are runtime artifacts. They are **gitignored** by this
folder's `.gitignore` and must not be committed.

## Why local-only

Phase final reports under `docs/runs/orchestrator_phase2a*_*/` are
the canonical, committed audit trail. Those reports may quote a
summary line from a gate run (`"all 17 gates green, 295/295
assertions"`) but the raw JSON is reproducible by re-running the
gates and adds noise to git history.

## Why phase-agnostic

Before Phase 2A-5 the default `-ReportPath` was hardcoded to
`docs/runs/orchestrator_phase2a2_review_runner_2026_05_06/hardening_gates.json`.
That meant every Phase 2A-3 / 2A-4 / 2A-5 / future-phase gate run
overwrote a Phase 2A-2 file -- wrong audit trail, accidental
diffs in unrelated commits.

Phase 2A-5 ARCH-006 fixes this: default reports go HERE, in a
phase-neutral folder, and the timestamp + git_branch + git_head_sha
fields in the JSON make each report self-describing.

## Schema

Each report JSON contains at minimum:

| Field | Meaning |
|---|---|
| `report_format_version` | currently `1` |
| `report_path`           | absolute path of this file |
| `latest_report_path`    | absolute path of the `latest.json` shortcut |
| `started_at_utc` / `finished_at_utc` | ISO-8601 UTC timestamps |
| `git_branch` / `git_head_sha` / `git_is_dirty` | repo state at run time |
| `powershell_version` / `os` | host info |
| `gates_run` / `gates_passed` / `gates_failed` | counts |
| `overall_ok` | boolean |
| `results` | per-gate array (gate name, ok, exit_code, elapsed_seconds, error) |

## Honoring `-ReportPath`

If a caller (CI, an operator, a phase doc generator) supplies an
explicit `-ReportPath`, the driver writes there exactly and still
records the path in `report_path` inside the JSON. Use this when
you DO want the report committed under a phase docs run dir as
part of an audit:

```
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\orchestrator\Run-WaggleHardeningGates.ps1 `
  -ReportPath .\docs\runs\orchestrator_phase2a5_fix_ledger_2026_05_06\hardening_gates.json
```

Final reports for a phase typically use this form once at the end,
to capture a definitive run.
