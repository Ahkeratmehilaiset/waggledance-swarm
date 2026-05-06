# Phase 2A-3 baseline

## Branch + commits

- Branch: `orchestrator/phase2a3-review-surface-hardening`
- Created from: `origin/main`
- origin/main SHA: `7d9722317bd0d5edaded9f25b2941081f8ddd68b`
  (Phase 2A-2 squash merge "feat(orchestrator): add safe Claude
  self-review runner (Phase 2A-2) (#90)")
- Local HEAD SHA: same as origin/main at fork time

## Pre-checkout cleanup (preserved, not destructive)

The working tree contained user data unrelated to Phase 2A-3:

1. Locally-modified tracked files (preserved via `git stash`):
   - `tools/waggle_backup.py`, `tools/waggle_restore.py`
   - `docs/runs/orchestrator_phase2a2_review_runner_2026_05_06/final_report.md`
     (the post-merge full final report -- the merged version is the
     "IN PROGRESS placeholder")
2. Untracked directories that conflict with tracked content on
   origin/main (Phase 16/17/18 docs from concurrent sessions),
   moved aside to a temp backup:
   - `docs/runs/phase16g_post_reboot_handoff_2026_05_03/`
   - `docs/runs/phase18d_local_delta_release_2026_05_05/`
   - `docs/runs/phase18e_runtime_gap_replay_2026_05_06/`
   - `docs/runs/phase18f_incremental_gap_replay_2026_05_06/`

The backup path is captured in `/tmp/wd_pre_phase2a3_backup.txt`. Both
sets of changes can be restored after Phase 2A-3 if the operator wants.

## Working tree summary at fork

Untracked items left in place (out-of-scope for this PR):

- `WD_release_to_main_master_prompt.md`
- `docs/atomic_flip_prep/03_HUMAN_APPROVAL.yaml`
- `docs/runs/orchestrator_phase2a2_review_runner_2026_05_06/post_merge_verification.md`
- `docs/runs/orchestrator_phase2a2_review_runner_2026_05_06/pr_body.md`
- `docs/runs/phase9_pr_body.md`
- `prompts/phase2a1_hardening.md`,
  `prompts/phase2a2_review_runner_production.md`

These were untracked before this session and remain untracked.

## Phase 2A-2 final report

Reachable on origin/main as the placeholder version that was committed
just before the squash merge:

```
docs/runs/orchestrator_phase2a2_review_runner_2026_05_06/final_report.md
-> "Status: IN PROGRESS — placeholder."
```

The full Phase 2A-2 final report I wrote post-merge is preserved in
the stash (described above) for reference; it does NOT need to land
on main as part of Phase 2A-3 (the Phase 2A-3 final report will
reference Phase 2A-2 by PR/SHA).

## Existing review evidence (the SEC-001 + empty-surface symptoms)

Two prior real-Claude review iterations exist locally:

| target iter         | architect | security        | reliability |
|---------------------|-----------|-----------------|-------------|
| `2026-05-06_19-45-54` | `pass` (files_reviewed=0, lines_reviewed=0) | `pass_with_notes` (3 findings, incl. **SEC-001**) | `pass` |
| `2026-05-06_20-32-48` | (same surface)                                | (same surface)                                 | (same)                |

### Symptom 1: empty review surface produces confident pass

`iterations/2026-05-06_19-45-54/reviews/architect.json`:

```json
{
  "role": "architect",
  "target_iteration_id": "2026-05-06_19-45-54",
  "verdict": "pass",
  "findings": [],
  "metrics": { "files_reviewed": 0, "lines_reviewed": 0, ... }
}
```

The reviewer correctly observed the package was essentially empty
(only run_metadata + git_metadata reached the reviewer's hands, no
source excerpts), but still emitted `verdict: "pass"`. A `pass` over
zero files is misleading. Phase 2A-3 P2/P3/P4 fix this in three
layers: (a) detect sparse package, (b) add a redacted source
supplement, (c) prompt the reviewer to refuse a confident pass when
the surface is empty.

### Symptom 2: SEC-001 conflated source-iteration metadata with review-mode metadata

`iterations/2026-05-06_19-45-54/reviews/security.md`, finding
SEC-001:

> "Runner invoked with --dangerously-skip-permissions while allowing
> Bash"
>
> where: run_metadata.json -> command_line

This was actually the **target smoke iteration's** run_metadata --
the operator's normal `Invoke-WaggleIteration` write-mode run, which
intentionally has `dangerouslySkipPermissions=true` and `allowBash=true`
in the live `orchestrator.config.json`. That is a write-mode setting
and is by design; it is NOT review-mode misconfiguration.

The review prompt did not distinguish "target iteration metadata"
(may have Bash) from "review-mode metadata" (must NOT have Bash).
Phase 2A-3 P4 makes that distinction explicit; Phase 2A-3 P1 + P1.5
add regression tests + a runtime safety gate that throw if review-
mode itself ever drifts unsafe.

## Phase 2A-2 status

Phase 2A-2 was MERGED via PR #90 (squash) into origin/main at
`7d9722317bd0d5edaded9f25b2941081f8ddd68b`. The runner works
structurally; Phase 2A-3 hardens the review surface and adds defense-
in-depth around the safety profile.

## Phase 2A-3 plan (high-level)

- P1: regression tests proving review-mode safety invariants
- P1.5: runtime safety gate `Assert-WaggleReviewSafeProfile` called
  immediately before subprocess launch
- P2: package quality model + sparse detection
- P3: review surface supplement (capped, redacted, dynamic fences)
- P4: prompt updates -- write-mode vs review-mode metadata,
  empty-evidence-is-a-finding, supplement disclosure
- P5: design doc
- P6: full hardening run with new tests
- P7: real smoke + 3 real reviews
- P8: commit / push / PR / CI / merge
- P9: final report (PASS if all green, HOLD otherwise)

P0 PASS.
