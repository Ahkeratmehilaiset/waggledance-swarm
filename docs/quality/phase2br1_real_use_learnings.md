# Phase 2B-R1 — real-use learnings primer

This file is the **primer** future Claude Code phases read before
touching the Phase 2B-Revision orchestration surface. It captures
what real use revealed that synthetic tests did **not** catch, and
what the operator-style ergonomics actually feel like when the
cockpit, ledger, classifier, matrix builder, controller, and Codex
scout are exercised against the real repo (rather than synthetic
fixtures).

Phase 2B-R1 is the first end-to-end self-epoch run of the new
infrastructure that landed under PR #94 (squashed at `9712de6`).
The notes below are first-hand operator notes from running each
component against the live `iterations/` tree on
`orchestrator/phase2br1-first-real-self-epoch`.

## What was intuitive in real use

* **Build-WaggleCockpitData → review_cockpit.html.** Generating
  `cockpit_data.json` and pointing a browser at
  `review_cockpit.html` is the most ergonomic single-shot for "what
  is the current state of pending bundles". The mental model
  `iteration → bundles[]` mirrors what's on disk. Nothing surprising
  there once the cockpit page loaded.
* **Get-WaggleFindingClass.** The classifier is the right amount of
  opinionated. Each call returns
  `class / fixability / severity / reason`, which is enough for
  routing decisions without becoming a black box. The `reason` field
  is the most valuable column for "why did the classifier route this
  here", and gives a clear explainer when a decision is challenged.
* **Build-WaggleAutoRepairPrompt.** The 11-rule scoped repair prompt
  with `max_files=2` is a compact contract. The prompt's existence
  is itself a signal that the bug is in scope for a local repair
  iteration; if the function refuses to emit one, that is also a
  decision the operator can act on.
* **Regression ledger trajectory display in the cockpit.**
  Monotonic-improvement-or-flat is easy to scan as a column;
  severity bands (info / low / medium / high / critical) decode
  faster than 0–100 raw scores when triaging at a glance.
* **Codex Scout import → matrix → synthesis paste-block.** The
  end-to-end is "drop a `codex_findings.json` in the iteration,
  run `Import-WaggleCodexFindings`, then re-run
  `Build-WaggleProposalMatrix`". The codex rows show up under
  `PM-CDEX-*` IDs with `source_kind=codex`, which is unambiguous.
  The synthesis paste block treating Codex as candidate evidence
  (per §2.6 of `synthesis_gpt.md`) is the right epistemic default.

## What was confusing in real use

* **`prompts/` vs `prompts_p5_test/`.** During the run there are now
  multiple prompts trees on disk, partially because of the p5_test
  scaffolding. A future phase MUST settle which is canonical and
  delete or namespace the others. The test scaffolding clutter is
  not a bug, but it is a daily papercut for any operator running
  the cockpit cold.
* **Bundle directory naming `claude_web_architect`.** The convention
  that the **last** underscore separates `provider` from `role` is
  not visible at a glance, especially when the provider itself
  contains an underscore (`claude_web`). The cockpit-data builder
  initially split on the **first** underscore (BWCD-BUG-001) and
  produced `(claude, web_architect)`, which silently passed
  schema validation. A small inline comment + the
  `Test-CockpitData.ps1` `cw3seg` test now defend this, but the
  convention itself is worth a one-line note in any future
  bundle-author docs.
* **`Get-Content -Raw` returning a PSObject in JSON.** The
  cockpit-data builder serialized `prompt_text` as
  `{value, PSPath, PSDrive: {…}}` instead of a plain string
  (BWCD-BUG-002). This is a PowerShell-specific gotcha — `Get-Content
  -Raw` can hand back something that *looks* string-shaped but
  carries provider metadata, and `ConvertTo-Json` walks it. Future
  phases should default to `[string](Get-Content -Raw …)` cast +
  `null`-guard at any boundary that hands the output to
  `ConvertTo-Json`.
* **Classifier `EXTERNAL_REVIEW_REQUIRED` for 1-line fixes.** The
  Invoke-WaggleReview top-level caller bug (INVK-BUG-001) is a
  textbook one-line fix, but the classifier routed it
  `EXTERNAL_REVIEW_REQUIRED / fixability_ambiguous` because the
  proposed-fix string described two possible shapes. **This is
  arguably correct conservative behavior** — the classifier should
  not declare "trivial" when the fix shape is genuinely under
  debate — but operators need to understand the routing depends on
  how the proposed-fix sentence is phrased, not on the actual code
  delta. If a future phase wants more LOCAL_REPAIR routing, it
  should tighten the proposed-fix wording on the input side, not
  loosen the classifier.
* **Old-shape internal reviews.** Real iteration trees still
  contain pre-SEC-009 architect reviews that have no
  `reviewer_self_id` and no `suggested_next_actions`. These are
  legitimate (Phase 2A-2 artifacts) and the schema validator must
  accept them — which it does — but the proposal matrix builder
  threw under StrictMode (BWP-BUG-001) because of a
  `$Obj.PSObject.Properties[X] | ForEach-Object { $_.Value }`
  pattern that fails to short-circuit on a missing key. Any future
  PowerShell code that walks optional JSON members must use the
  explicit null-guard pattern documented in BWP-BUG-001.
* **`Invoke-WaggleReview -DryRun` exit code.** Dry-run mode
  successfully renders the prompt and `package_quality.json` but
  exits 1 because the top-level caller dereferences `$r.role` on a
  pscustomobject that lacks `role`. Operators using `-DryRun` for
  prompt-shape testing should expect the staging directory to be
  populated even when the caller exits 1; do not classify dry-run
  exit-1 as "the prompt failed to render".

## What synthetic tests didn't catch

| Bug | Synthetic test had | Real-repo data had | Why missed |
|-----|--------------------|--------------------|-------------|
| BWCD-BUG-001 | `gemini_architect`, `grok_reliability` (2-segment names) | `claude_web_architect` (3-segment) | Fixture was uniform on 2-segment; never exercised the 3-segment edge case. |
| BWCD-BUG-002 | inline `Out-File` content, plain string | `Get-Content -Raw` provider object | Inline content masked the PSObject behavior of `-Raw`. |
| CLF-BUG-001 | evidence text `expected X but got Y` | `expected key: foo_count actual: fooCount` | Synthetic strings used the conjunctive form; the colon-prefixed real-world form was outside the regex. |
| BWP-BUG-001 | new-shape (with `suggested_next_actions`) only | old-shape Phase 2A-2 review without it | StrictMode interaction with missing JSON members didn't fire on the new-shape happy-path fixture. |
| INVK-BUG-001 | `Invoke-WaggleReview` invoked indirectly via Test-Phase2A2 | direct CLI invocation `-DryRun` | The harness reads the function return directly and skips the top-level Write-Host caller. |

The pattern: **synthetic tests covered the canonical happy path
and the new schema, but real iterations carry legacy shapes,
edge naming, and OS-specific I/O behavior that synthetic fixtures
were uniformly clean of.** Any future phase that adds new orchestrator
surface should also add a fixture that intentionally mixes old-shape
+ new-shape JSON, multi-underscore provider names, and `Get-Content
-Raw` boundaries.

## UI flows that were rough

* **Cockpit ledger trajectory column.** When the ledger is empty
  the column reads "no trajectory yet", which is correct but reads
  as "ledger broken" at a glance. A grayed-out "—" with a tooltip
  ("ledger has 0 epochs") would be friendlier.
* **Proposal matrix ID prefixes.** `PM-CDEX-*` (codex) vs
  `PM-INTR-*` (internal) vs `PM-EXTR-*` (external) is unambiguous
  but tightly abbreviated. A future cockpit row should expand
  these in a tooltip ("PM-CDEX = proposal matrix entry from Codex
  scout").
* **Classifier output table.** The classifier's per-finding
  `class / fixability / severity / reason` is great for one
  finding, but at scale (10+ findings) the operator is reading 4
  short strings × 10 rows in a fixed-width terminal. A `--summary`
  flag emitting just `class` counts would help triage.
* **Auto-repair prompt scope ergonomics.** The 11-rule prompt is
  compact, but `max_files=2` is a hard ceiling that some genuinely
  trivial fixes (e.g., adding a tiny null-guard + a 1-line test)
  will hit. The current behavior (refuse to emit) is correct
  conservative behavior, but operators should know to bump `-MaxFiles`
  to 3 for trivial-fix-with-test cases.

## Configuration defaults to adjust

* **`max_files=2` for `Build-WaggleAutoRepairPrompt`** — leave at
  2 for now; operators can override per-call. Re-evaluate if the
  classifier's TRIVIAL_AUTO_FIX route refuses too often in the
  next phase's traffic.
* **`Test-CockpitData` fixtures** — keep both 2-segment and
  3-segment provider names in the fixture set permanently.
* **`Test-FindingClassifier` fixtures** — keep the
  punctuation-prefixed `actual:` / `actual,` / `actual;` cases
  permanently to lock in the regex's loosened boundary.
* **`orchestrator.config.json` `model: "opus"`** — confirmed real
  in this run; aligned with CLAUDE.md rule 8 (strongest-model
  default). No change needed.

## Docs to update

* **`docs/orchestrator/AUTO_REPAIR_FLOW.md`** (if it exists) —
  add a "classifier verbatim verdict is the operator's verdict"
  note, plus the proposed-fix-wording dependency described above.
* **`docs/orchestrator/COCKPIT.md`** (if it exists) — document
  the bundle-name parsing convention (last underscore separates
  provider from role).
* **Any `prompts/review/*.md`** — confirm SEC-009 fields
  (`reviewer-self-id`, `suggested_next_actions`) are described as
  REQUIRED-when-present, OPTIONAL-when-absent, with the validator
  accepting both shapes. The Phase 2B-R `architect.md` prompt
  already does this; future role prompts must match.
* **`docs/runs/orchestrator_phase2br1_self_epoch_2026_05_07/final_report.md`**
  (P12 output) — link this primer in the "Reading order for the
  next phase" section.

## How to read this primer

If you are a future Claude Code phase touching the orchestration
surface, before you write code:

1. Skim "What was confusing" and "What synthetic tests didn't
   catch" — both are highest-signal.
2. Read the routing log in
   `docs/runs/orchestrator_phase2br1_self_epoch_2026_05_07/classifier_runs.md`
   to see how each P9-era bug was actually classified.
3. If you are extending the cockpit / matrix / ledger / classifier
   / controller / codex scout: add at least one fixture that
   represents real legacy shape AND new shape, and at least one
   fixture for the awkward naming case relevant to your component.
4. If your work would change the auto-repair classifier's routing
   rules: do not loosen the regex; tighten the proposed-fix
   wording on the input side instead. The classifier is the
   conservative arbiter on purpose.

This primer is committed under `docs/quality/` so that future phases
can reach it without consulting the run-specific `docs/runs/...`
folder.
