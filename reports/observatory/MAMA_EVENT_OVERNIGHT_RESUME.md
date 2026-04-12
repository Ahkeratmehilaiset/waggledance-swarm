# Mama Event Observatory — Overnight Resume Audit Trail

This document records the safe-resume of the Mama Event Observatory
working tree onto the correct branch before the overnight real-data
collection run begins. It exists because a branch detour happened and
the project rules require any branch detour to be visible in the audit
trail.

## What happened

During an unrelated `/simplify` skill invocation against the
`feat/v357-feed-runtime-wiring` release branch, the working tree on
`feat/mama-event-observatory` was stashed (`git stash -u`) and the
checkout switched to `feat/v357-feed-runtime-wiring`. The simplify
review touched only v357 release files and did **not** touch any
observatory code or any observatory artifact.

Branch state at the moment of the detour:

* Observatory branch HEAD: `08c3608 feat(observatory): add long-run driver + real-data validation`
* Stashed entry: `WIP on feat/mama-event-observatory: 08c3608 …`
* The stash contained an obsolete older draft of `x.txt` that had
  already been superseded by the current overnight task instructions.

## Recovery sequence

1. **Commit the simplify work on its proper branch first.** The five
   v357 release files (`status.py`, `auth.py`, `settings_loader.py`,
   `start_runtime.py`, `start_waggledance.py`) were committed on
   `feat/v357-feed-runtime-wiring` as commit `932d5f4` (`polish:
   second simplify review on post-gate commits`). `x.txt` was
   deliberately excluded from that commit because it is the active
   task instruction file, not part of any branch's release scope.
2. **Verify `x.txt` is identical at HEAD on both branches.** Both
   branches resolved `x.txt` at HEAD to the same blob, so checking
   out `feat/mama-event-observatory` migrated the working-tree
   modifications cleanly with no conflict.
3. **Checkout `feat/mama-event-observatory`.** Working tree returned
   to the observatory branch with `x.txt` updated to the current
   overnight task spec and no other modifications.
4. **Drop the stale stash.** The stash held an obsolete `x.txt`
   draft only; nothing in `waggledance/`, `tools/`, `tests/`,
   `reports/`, or `logs/` was at risk. After confirming this, the
   stash was dropped instead of applied to avoid re-introducing the
   obsolete instructions.
5. **Re-run observatory test suite.** All 185 observatory tests
   passed in 0.57 s. No regression from the v357 polish work could
   leak into the observatory branch because the simplify commit
   never landed here.

## Post-recovery state

| Item                                            | Value |
|------------------------------------------------|-------|
| Active branch                                  | `feat/mama-event-observatory` |
| Branch HEAD                                    | `08c3608 feat(observatory): add long-run driver + real-data validation` |
| Working-tree modifications                     | `x.txt` only (active task spec) |
| Untracked files                                | none |
| `tests/observatory/` result                    | 185 passed in 0.57 s |
| Existing reports under `reports/observatory/`  | 6 files (FRAMEWORK, BASELINE, ABLATIONS, CANDIDATES, GATE, LONGRUN) |
| Existing NDJSON under `logs/observatory/`      | baseline + longrun PASS A / PASS B artifacts |
| `data/chat_history.db` row count               | 124 messages (max id 124) |
| Overnight log directory                        | `logs/observatory/overnight/` (to be created by collector) |

## Hard rules acknowledged for the overnight run

* No remote operations, no `git push`, no PR, no merge, no tag.
* No edits to release-branch files on `feat/v357-feed-runtime-wiring`.
* No mixing of synthetic data into the overnight final verdict.
* No edits to `ScoreBand` or `GateVerdict` enums.
* No relaxation of existing honesty invariants.
* `assert_no_hype` will be called against every report string before
  it is written to disk.
* If the overnight run produces zero new real candidate events, that
  is an accepted outcome and will be reported as such — separated
  cleanly into `data_status` and `observatory_verdict`.

## Next steps

The overnight collector (`tools/mama_event_overnight.py`) and the
morning analysis tool (`tools/mama_event_overnight_analysis.py`) will
be built on this branch. They will read only real data from
`data/chat_history.db` via a row-id checkpoint and will write all
artifacts under `logs/observatory/overnight/`. Tests for the
collector go under `tests/observatory/`. A single closeout commit
will land at the end of the overnight cycle once the morning
analysis has completed.
