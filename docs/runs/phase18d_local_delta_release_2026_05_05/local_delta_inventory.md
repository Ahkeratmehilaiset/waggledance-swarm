# Phase 18D — Local Delta Inventory

**Date (UTC):** 2026-05-06
**Method:** `git worktree list`, `git branch -vv`, `git status --short` per worktree, `git log --branches --not --remotes --oneline`.

## Worktree topology

19 worktrees in total:

| Path | Branch | Dirty? |
| --- | --- | --- |
| `C:/Python/project2` | `phase8.5/dream-curriculum` | yes (untracked) |
| `C:/Python/project2-a` | `phase8.5/curiosity-organ` | clean |
| `C:/Python/project2-b` | `phase8.5/self-model-layer` | clean |
| `C:/Python/project2-d` | `phase8.5/hive-proposes` | yes (untracked) |
| `C:/Python/project2-flip` | `phase9/post-campaign-atomic-flip` | clean |
| `C:/Python/project2-master` | `main` (stale, behind origin/main) | yes (modified + untracked) |
| `C:/Python/project2-phase16f-docker-stable-gate` | `phase16g/post-stable-ci-truth` | clean |
| `C:/Python/project2-phase17a-producer-fabric-scale` | `phase17a/post-release-docs` | clean |
| `C:/Python/project2-phase17b-local-efficiency-benchmark` | `phase17b/post-release-docs` | clean |
| `C:/Python/project2-phase17c-local-ollama-baseline` | `phase17c/local-ollama-baseline` | clean |
| `C:/Python/project2-phase17c-post-release-docs` | `phase17c/post-release-docs` | clean |
| `C:/Python/project2-phase17d-local-model-sweep` | `phase17d/local-model-sweep` | clean |
| `C:/Python/project2-phase17d-post-release-docs` | `phase17d/post-release-docs` | clean |
| `C:/Python/project2-phase18a-benchmark-externalization` | `phase18a/benchmark-externalization-schema` | clean |
| `C:/Python/project2-phase18a-post-release-docs` | `phase18a/post-release-docs` | clean |
| `C:/Python/project2-phase18b-gap-miner-feedback` | `phase18b/gap-miner-feedback` | clean |
| `C:/Python/project2-phase18b-post-release-docs` | `phase18b/post-release-docs` | clean |
| `C:/Python/project2-phase18c-mined-solver-dispatch` | `phase18c/post-release-docs` | yes (untracked) |
| `C:/Python/project2-r7_5` | `phase8.5/vector-chaos` | clean |

## Unpushed commits across all branches

`git log --branches --not --remotes --oneline` returned exactly:

```
847e6bd (phase18c/mined-solver-runtime-dispatch) Merge branch 'main' of https://github.com/Ahkeratmehilaiset/waggledance-swarm into phase18c/mined-solver-runtime-dispatch
```

This single dangling commit is a local merge of origin/main into the phase18c/mined-solver-runtime-dispatch branch made during the Phase 18C session. The Phase 18C content already reached origin/main as squash-merge `e9aa1de1` (PR #83), so this local merge commit is effectively orphaned and carries no new content. **DROP_GENERATED** (not relevant; obsoleted by squash-merge).

## Dirty worktrees — file-level inventory

### `C:/Python/project2-master` (branch `main`, behind origin/main by 18 commits)

Modified (tracked):

| File | Diff size | Nature |
| --- | --- | --- |
| `tools/waggle_backup.py` | 19 lines | Docstring/changelog only — adds `v9.2:` line referencing Phase 16D / `v3.7.8-docker-gate-alpha` |
| `tools/waggle_restore.py` | 18 lines | Docstring/changelog only — bumps header version to `v3.7.8.0` and adds matching changelog line |

Untracked:

| Path | Nature |
| --- | --- |
| `.claude/scheduled_tasks.lock` | Local Claude Code session lock |
| `.claude/settings.local.json` | Local Claude Code settings |
| `WD_release_to_main_master_prompt.md` | Operator-pasted master prompt (release-to-main session, Strategy A) |
| `docs/atomic_flip_prep/03_HUMAN_APPROVAL.yaml` | DRAFT HUMAN_APPROVAL artifact for Phase 9 atomic flip — not signed, not collected, header reads "Human Approval Artifact — DRAFT" |
| `docs/runs/phase16g_post_reboot_handoff_2026_05_03/HANDOFF.md` | Phase 16G session handoff document (153 lines, 2026-05-03) describing Docker Desktop install + WSL2 reboot pending; written by previous Claude Code session before user-initiated reboot. The Docker setup it describes is now resolved (Phase 16D/16F/18B/18C Docker `--network none` proofs all PASS). |
| `docs/runs/phase9_pr_body.md` | Draft PR body for Phase 9 review-only PR (PR #51 already squash-merged) — historical text |

### `C:/Python/project2-phase18c-mined-solver-dispatch` (branch `phase18c/post-release-docs`)

Untracked:

| Path | Nature |
| --- | --- |
| `docs/runs/phase18c_mined_solver_runtime_dispatch_2026_05_05/final_report.md` | Phase 18C end-of-session final report (timeline + 8-tag verification + honesty contracts). Written immediately after PR #84 was merged at 2026-05-05T19:01:17Z; never made it into PR #84 because PR #84 was already merged. Standalone release-audit artifact for the now-complete v3.10.2 release. |

### `C:/Python/project2` (branch `phase8.5/dream-curriculum`) — phase8.5 lineage

Untracked (sample):

* Root-level operator notes: `A.txt`, `B.txt`, `C.txt`, `D.txt`, `Prompt_1_Master_v5_1.txt`, `R7_5.txt`, `wd_phase10_master_prompt_rewritten.md`
* `docs/journal/2026-04-27_pr_strategy.md` (operator journal entry)
* `docs/runs/RESUME_INSTRUCTIONS.md`, `docs/runs/final_release_handoff.md`, `docs/runs/overnight_soak_recheck.md`, `docs/runs/promotion_latest.md`, `docs/runs/smoke_recheck_latest.md` (operator notes / session resume docs)
* `docs/runs/curiosity/`, `docs/runs/gap_miner/`, `docs/runs/self_model/` (phase8.5-era run output trees)
* `docs/runs/ui_gauntlet_20260412/{chat_ui_results.jsonl,harness_results.json,mixed_soak_metrics.json,query_corpus.json}` (Apr-12 UI gauntlet outputs)
* `docs/runs/ui_gauntlet_400h_20260413_092800/{.auto_commit.pid,cold.pid}` (PID files from a 400h UI gauntlet run)

This worktree is on the **`phase8.5/dream-curriculum` lineage**, not the `main` release lineage. Phase 18D is a release sprint on `main`; phase8.5 work is a separate parallel exploration track and is not in scope for this release.

### `C:/Python/project2-d` (branch `phase8.5/hive-proposes`) — phase8.5 lineage

Untracked:

| Path | Nature |
| --- | --- |
| `docs/runs/hive/HISTORY.jsonl` | Phase8.5 Hive Proposes history log (JSONL append-only). Phase8.5 lineage. |

## Other branches (`git branch -vv` summary)

Many local branches show `[origin/main: ahead N, behind M]` — the "ahead" commits on those branches were squash-merged into origin/main and then "ahead" reflects the unsquashed pre-merge tips (still preserved on the per-branch refs `origin/<branch>`). They are not unpushed work; they are squash-superseded history. The single truly unpushed commit confirmed by `--not --remotes` is `847e6bd` above.

## Summary of distinct local artifacts not on GitHub

1. `tools/waggle_backup.py` (M, master worktree) — docstring-only
2. `tools/waggle_restore.py` (M, master worktree) — docstring-only
3. `docs/runs/phase18c_mined_solver_runtime_dispatch_2026_05_05/final_report.md` (untracked, phase18c worktree) — release audit doc
4. `WD_release_to_main_master_prompt.md` (untracked, master worktree) — operator-pasted prompt text
5. `docs/atomic_flip_prep/03_HUMAN_APPROVAL.yaml` (untracked, master worktree) — DRAFT Stage-2 approval template
6. `docs/runs/phase16g_post_reboot_handoff_2026_05_03/HANDOFF.md` (untracked, master worktree) — historical session handoff
7. `docs/runs/phase9_pr_body.md` (untracked, master worktree) — historical Phase 9 PR body draft
8. `.claude/` (untracked, master worktree) — local Claude Code session metadata
9. Phase 8.5 worktree contents (project2 + project2-d untracked sets) — parallel-lineage operator and run-output material
10. Local merge commit `847e6bd` on phase18c/mined-solver-runtime-dispatch — obsoleted by squash-merge

No tracked or untracked content was found that contains a live token, password, API key, private key, or other secret-shaped string.
