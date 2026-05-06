# Phase 2A-5 -- phase-fix ledger and hardening-gates reporting

Last small pre-Phase-2B cleanup. Two surgical changes:

## 1. Phase-fix ledger (ARCH-005, this phase)

### Problem statement

Phase 2A-3 and Phase 2A-4 added many fix-tag comments in the
orchestrator source: `# Phase 2A-4 REL-003: ...`,
`# Phase 2A-4 ARCH-001: ...`, etc. There was no central audit
table mapping these tags to:

- the source anchors that contain the fix,
- the tests that prove the fix,
- whether the tag is `fixed` / `false_positive_due_to_truncation` /
  `already_fixed` / `backlog` / `informational`,
- which phase introduced the finding and which phase fixed it.

This made future audits hard. Reviewers re-read every Phase 2A-N
final report; operators chasing "is REL-004 actually closed?" had
to grep the codebase.

### Why a ledger needs a disambiguation key

Each review run (Phase 2A-3 architect, Phase 2A-4 reliability,
etc.) numbers its findings from 0. So the same tag-ID has DIFFERENT
meanings across phases:

| Tag-ID | Phase 2A-3 architect | Phase 2A-4 architect |
|--------|----------------------|----------------------|
| ARCH-001 | "Redactor self-corrupts" | "subprocess runner duplicated" |

The unique key is `(phase_introduced, tag)`, NOT bare tag.
`Test-PhaseFixLedger.ps1` enforces this: it greps source for
`Phase 2A-N (ARCH|REL|SEC)-N` and matches each occurrence to a
ledger row with the matching `(phase_fixed_or_documented OR
phase_introduced, tag)` pair.

### Schema

`docs/design/phase_fix_ledger.json` is the **source of truth**.
`docs/design/phase_fix_ledger.md` is a rendered human view; both
must agree (Test-PhaseFixLedger asserts the markdown has at least
one table row per JSON entry).

JSON columns:

| Column | Meaning |
|---|---|
| `tag` | `ARCH-NNN` / `REL-NNN` / `SEC-NNN` |
| `title` | one-line headline |
| `source` | `architect` / `reliability` / `security` / `human-review` / `final-report` |
| `status` | `fixed` / `false_positive_due_to_truncation` / `already_fixed` / `backlog` / `not_reproducible` / `informational` |
| `phase_introduced` | `Phase 2A-3` etc. -- the phase whose review surfaced the finding |
| `phase_fixed_or_documented` | the phase that closed it (or backlogged it) |
| `canonical_source_anchors` | array of `path :: stable_text` strings |
| `tests` | array of test-script paths |
| `notes` | prose -- backlog rows MUST mention `Acceptance` and a future `Phase` |

### Anchor format

`path :: stable_text`. Line numbers drift; stable text doesn't. The
text after `::` must appear somewhere in the file. Reserved /
informational rows use `path :: tag` self-references so the test
still passes.

### Reserved rows

The master prompt requires the ledger to cover:

- ARCH-000..ARCH-006
- REL-000..REL-009
- SEC-000..SEC-007

Some of these slots have no real finding yet. They get
`status = informational`, `title = "Reserved"` rows so the ledger
has a stable shape from this phase onward. Future phases that
issue a real finding for a reserved slot replace the row.

## 2. Hardening-gates report path (ARCH-006)

### Problem statement

`orchestrator/Run-WaggleHardeningGates.ps1` defaulted `-ReportPath`
to `docs/runs/orchestrator_phase2a2_review_runner_2026_05_06/hardening_gates.json`.
Every Phase 2A-3 / 2A-4 / 2A-5 / future-phase gate run overwrote
that **Phase 2A-2** file. Wrong audit trail; accidental diffs in
unrelated commits.

### Phase 2A-5 fix

Default `-ReportPath` is now phase-agnostic:

```
docs/runs/hardening_gates/<UTC_timestamp>.json
```

with `2026-05-07T00-30-15Z.json` shape (filesystem-safe; no `:`).

A sibling `latest.json` is a `Copy-Item` of the most recent run
(no symlinks, because Windows symlinks may need elevated
permissions).

### Git hygiene for generated reports

`docs/runs/hardening_gates/` contains:

- `README.md` (committed) -- explains the directory's purpose
- `.gitignore` (committed) -- ignores `*.json`, exempts
  `README.md` and `.gitignore` itself
- `latest.json` (gitignored, local-only)
- `<UTC>.json` reports (gitignored, local-only)

Final phase reports may QUOTE summaries from a gate JSON; raw
JSONs do not need to be committed. If a particular phase needs to
commit a definitive run, it passes an explicit `-ReportPath` under
its own `docs/runs/orchestrator_phase2a*_*` folder.

### Honoring `-ReportPath`

If the caller supplies `-ReportPath`, the driver writes there
exactly and still records the path in `report_path` inside the
JSON. Both the explicit path and the timestamped default produce
the same `latest.json` shortcut.

### Rich JSON metadata

Each report includes:

- `report_format_version` (currently `1`)
- `report_path`, `latest_report_path`
- `started_at_utc`, `finished_at_utc`
- `git_branch`, `git_head_sha`, `git_is_dirty`
- `powershell_version`, `os`
- `gates_run`, `gates_passed`, `gates_failed`, `overall_ok`
- per-gate `results` (gate name, ok, exit_code, elapsed_seconds, error)

Self-describing: a stranded `latest.json` tells you which branch /
SHA / OS / PS-version produced it.

### `-SelfTest` mode

To test path-generation behavior without spending ~30s running
every gate, the driver accepts `-SelfTest`. It emits the resolved
ReportPath / latest path / phase-agnostic flag as JSON on stdout
and exits 0 without running any gate.
`Test-HardeningGatesReportPath.ps1` uses this.

## Tests

| Test | Cases | What it asserts |
|---|---|---|
| `Test-PhaseFixLedger.ps1`       | 17 | ledger JSON parses, required tag-number ranges present, every `Phase 2A-N (TAG)-N` source reference has a row, fixed/already_fixed rows have anchors + tests, anchor files exist + text appears, backlog rows have future-phase notes, ARCH-005 / ARCH-006 wiring |
| `Test-HardeningGatesReportPath.ps1` | 25 | driver parses, has the canonical "default ReportPath" anchor + Phase 2A-5 ARCH-006 tag, no live `$ReportPath = ...` assignment references the old phase-2a2 path, `-SelfTest` returns JSON with phase-agnostic UTC-timestamped path + `latest.json` sibling, `-ReportPath` override is honored, `hardening_gates/` has README + .gitignore, generated reports are gitignored |

Both gates run inside `Run-WaggleHardeningGates.ps1`, in the
deterministic order documented in the gate-list comment.

## Phase 2B handoff

`prompts/phase2b_handoff_requirements.md` (NEW in this phase) is
the persistent contract Phase 2B's prompt must satisfy:

1. update `phase_fix_ledger.{json,md}` for every new ARCH/REL/SEC
   tag introduced or carried forward;
2. use the phase-agnostic `docs/runs/hardening_gates/<utc>.json`
   default ReportPath, NOT a new phase-specific default;
3. keep `docs/runs/hardening_gates/.gitignore` strict (no committed
   generated reports);
4. final report quotes the gate timestamped report path used for
   the definitive run, lists ledger rows added/updated, and lists
   any new backlog tags created with future-phase + acceptance
   criteria;
5. no browser automation, no third-party LLM UI automation, no tag,
   no release;
6. ledger discipline is enforced by `Test-PhaseFixLedger`.

## What this phase does NOT do

Per master prompt rule 2:

- Does NOT touch `Invoke-WaggleIteration.ps1`,
  `Invoke-WaggleReview.ps1`,
  `lib/review/ReviewAdapter.ps1`,
  `lib/review/ReviewSurface.ps1`,
  `lib/CompletionVerifier.ps1`,
  `lib/ArtifactValidator.ps1`,
  `lib/Redactor.ps1`.
- Does NOT change review semantics.
- Does NOT re-open Phase 2A-4 fixes.

P9 of this phase ran the full gates and confirmed every Phase 2A-1
through 2A-4 invariant still holds: 17/17 gates green; the new
`Test-PhaseFixLedger` (17/17) and `Test-HardeningGatesReportPath`
(25/25) added cleanly without affecting any prior gate.
