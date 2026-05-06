# Phase 2A-3 -- review surface and review-mode safety hardening

## Problem statement

Phase 2A-2 merged a working safe Claude self-review runner. Real
self-reviews against two smoke iterations
(`2026-05-06_19-45-54`, `2026-05-06_20-32-48`) revealed two issues:

1. **Empty review surface yields confident pass.** `architect.json`
   recorded `files_reviewed: 0`, `lines_reviewed: 0`, `verdict:
   "pass"`. The reviewer correctly noted there were no source files
   in the package, but still emitted a confident pass. A pass over
   zero files is misleading.
2. **SEC-001 false positive.** `security.md` flagged the
   `run_metadata.json` of the **target write-mode smoke iteration**
   (which legitimately had `--dangerously-skip-permissions` +
   `--allowed-tools Bash`) as if it were review-mode metadata. The
   prompt did not distinguish "target iteration metadata" (may have
   Bash by operator design) from "review-mode metadata" (must NEVER
   have Bash).

## SEC-001 interpretation

The Phase 2A-2 review prompt did not communicate that the target
iteration's metadata can legitimately enable Bash and dangerously-
skip-permissions. So when the security reviewer saw a smoke run's
metadata, it raised a finding even though that metadata was about a
**different process** -- the write-mode smoke -- not the review-mode
child Claude.

Resolution (Phase 2A-3 P4): every review prompt now explicitly
distinguishes the two metadata files. Write-mode `run_metadata.json`
having Bash is NOT a finding by itself. Review-mode metadata
(`iterations/<id>/reviews/<role>.metadata.json`) violating any
safety invariant IS a critical/high finding.

## Empty-package root cause

The orchestrator's smoke flow produces an `llm_input_package.md`
that contains run_metadata, git_metadata, the report file, the PS
tail, the Claude stdout/stderr. None of these are source code for
the orchestrator itself. So when the review runner embeds that
package as the only evidence the reviewer sees, the reviewer
correctly observes "no source surface" -- but with the original
prompt, it still emitted a confident pass.

Resolution (Phase 2A-3 P2/P3): the runner now (a) computes a small
`package_quality.json` record on every review, (b) detects sparse
packages, (c) builds a redacted, capped, dynamically-fenced "review
surface supplement" from the orchestrator's own source tree and
appends it to the prompt, (d) returns `NEEDS_REVIEW_SURFACE` if the
package is sparse AND the working tree did not even have source
files for a supplement.

## Package quality model

`Get-WaggleReviewPackageQuality` reads `llm_input_package.md` and
emits:

| Field                    | Meaning                                  |
|--------------------------|------------------------------------------|
| `package_chars`          | total chars in the package               |
| `package_lines`          | total newline-separated lines            |
| `section_count`          | number of `## ` / `### ` headings        |
| `source_section_count`   | headings whose title matches a "source/test/schema/prompt" pattern |
| `reviewable_files_count` | unique file paths mentioned in source sections or list-item lines |
| `reviewable_lines_count` | lines of body text inside fenced code blocks under a source-titled section |
| `section_titles`         | the heading list, for diagnostics        |

## Sparse threshold

A package is considered **sparse** when:

- `source_section_count == 0` (no source/test/schema/prompt section
  at all)

OR

- `reviewable_files_count < 3` AND `reviewable_lines_count < 150`

The conjunction in the second case is deliberate: a package with
many lines but only one section, or with many sections but few
lines, has at least one strong axis of evidence and is not
considered sparse. Empty-on-both-axes is sparse.

## Supplement model

`Get-WaggleReviewSurfaceSupplement` builds a markdown block that
the runner appends to the review prompt when the package is sparse.
Properties:

- **Capped.** Per-file cap (default 6000 chars), total cap (default
  80000 chars), max files (default 40). Defaults can be overridden.
- **Redacted.** Every byte runs through `Invoke-WaggleRedaction`
  before embedding (Phase 2A-1 redactor; SHA allowlist preserved).
- **Dynamic fences.** `_Get-DynamicFence` chooses a backtick run
  (3 -> 4 -> 5 -> 6 -> 7) that is NOT present in the body, so the
  embedded source cannot break out of its fence.
- **Untrusted-data labels.** Every section header includes
  "(UNTRUSTED DATA)"; every prompt template explicitly tells the
  reviewer to treat the supplement as untrusted evidence.
- **Deterministic ordering.** A hardcoded file list (runners, libs,
  review libs, tests, schemas, prompts) -- order is fixed regardless
  of mtime / size.
- **Truncation markers.** Files truncated at the per-file cap are
  marked with an explicit `> NOTE: this excerpt was truncated to N
  characters; original size on disk was M bytes.` line.
- **Skipped markers.** Files exceeding the total cap, missing on
  disk, or past the max-files count get a "Surface files SKIPPED"
  appendix listing them with a reason.

## NEEDS_REVIEW_SURFACE

If the package is sparse AND the supplement could not be built
(no orchestrator/lib source on disk -- e.g. someone's running the
review runner from a stripped checkout), the runner refuses the run
with `status: NEEDS_REVIEW_SURFACE`. It writes a metadata file
naming the failure, does not spawn Claude, and exits non-zero.
This prevents the "confident pass over empty" failure mode.

## Review-mode safety invariants (P1 + P1.5)

Phase 2A-2 already had two layers:

1. `orchestrator.config.review.example.json` -- the committed safe
   profile.
2. `Resolve-WaggleReviewEffectiveProfile` -- hard-clamps in code
   regardless of what review config is loaded.

Phase 2A-3 adds two more:

3. `Test-WaggleReviewSafeProfileViolations` -- pure-function
   predicate over the effective profile + arglist. Used by
   regression tests and the runtime gate. Single decision point
   for the safety invariants.

4. `Assert-WaggleReviewSafeProfile` -- runtime gate called
   immediately before `Invoke-WaggleReviewSubprocess`. Throws if any
   of: `allow_bash` true, `dangerously_skip_permissions` true,
   `sanitize_environment` false, `require_unique_artifact` true,
   `Bash`/`Write`/`Edit` in `allowed_tools`, `Bash`/`Write`/`Edit`
   missing from `disallowed_tools`, `--dangerously-skip-permissions`
   in arglist, `Bash`/`Write`/`Edit` in `--allowed-tools` cli value.

Belt + braces: if any future regression breaks the validator or
the config-clamp, the runtime gate kills the run before the child
is spawned.

## Tests

| Test                       | Cases | Coverage                                    |
|----------------------------|-------|---------------------------------------------|
| `Test-Phase2A2.ps1`        | 56    | DoD invariants + runtime-gate wiring        |
| `Test-ReviewSchema.ps1`    | 16    | schema validator                            |
| `Test-ReviewAdapter.ps1`   | 38    | adapter incl. supplement-aware prompt build |
| `Test-ReviewRunner.ps1`    | 69    | runner end-to-end via fake-claude           |
| `Test-ReviewSafety.ps1`    | 25    | NEW: profile violations + runtime gate      |
| `Test-ReviewSurface.ps1`   | 22    | NEW: package quality + sparse + supplement  |
| `Test-Redaction.ps1`       | 27    | redactor regression                         |
| `Test-Redactor.ps1`        | 26    | redactor                                    |
| `Test-SmokeValidation.ps1` | 16    | unique-artifact contract                    |
| `Test-Syntax.ps1`          | 47 files | PS 5.1 parser preflight                  |

`Run-WaggleHardeningGates.ps1` drives all 10 gates in deterministic
order and writes `hardening_gates.json`.

## Remaining risks

1. The supplement is built deterministically from a hardcoded file
   list. If a future Phase adds a new source file that should be
   reviewable, `Script:ReviewSurfaceFiles` in `ReviewSurface.ps1`
   needs to be updated.
2. The sparse threshold is heuristic. A package with one large file
   but otherwise empty might still slip past as "non-sparse" if the
   single file pushes `reviewable_lines_count >= 150`. That is
   intentional -- one strong axis is OK -- but operators should
   still spot-check architect/security/reliability summaries.
3. `Invoke-WaggleReviewSubprocess` remains a Phase 2A-2 special-case
   workaround for PS 5.1 async-event stdout loss. It is NOT
   refactored in this phase (per master prompt rule 2). Phase 2A-4
   may revisit.
4. The supplement uses dynamic fences but does NOT enforce a unique
   delimiter sequence beyond backticks. Crafted source containing
   every variant of backtick run from 3 to 7 cannot break out of
   the fence (we'd grow to 8+); but a maliciously-crafted file with
   backtick runs of length >= 10 is theoretically a problem. In
   practice no orchestrator source has that.

## Definition of Done

- 10/10 hardening gates green
- Real Claude smoke + 3 reviews all COMPLETE
- Architect / Reliability `files_reviewed > 0` (because supplement
  populates the surface)
- Security review does NOT raise SEC-001 against the target's
  write-mode metadata
- All three reviews mention in their `summary` that evidence came
  from the supplement (when one was applied)
- `package_quality.json` exists for every review
- Review-mode metadata's safety profile passes
  `Test-WaggleReviewSafeProfileViolations`
- PR opened, CI green, squash-merge with `--match-head-commit`
- No tag, no release, no Phase 2B start
