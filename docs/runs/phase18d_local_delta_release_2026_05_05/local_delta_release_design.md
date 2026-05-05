# Phase 18D — Local Delta Release Design

**Status:** design (BEFORE-CODE) — written 2026-05-06.
**Inputs:** `baseline_verification.md`, `local_delta_inventory.md`, `local_delta_classification.md`, `session_state.json`.

## Mission scope (constrained)

Phase 18D's bounded mission is to publish **only** the local improvements that are coherent, tested-where-applicable, non-secret, non-DB, non-temporary, and non-incomplete, while parking everything else. Per the inventory and classification, the entire local delta on `main` lineage reduces to:

1. `tools/waggle_backup.py` — pure docstring/changelog update (v9.1 → v9.2 header).
2. `tools/waggle_restore.py` — pure docstring/changelog update (v3.5.7.1 → v3.7.8.0 header).
3. `docs/runs/phase18c_mined_solver_runtime_dispatch_2026_05_05/final_report.md` — release-audit document for the now-completed v3.10.2 release; written immediately after PR #84 merged but never pushed.

That is the **entire** in-scope delta for Phase 18D. It is docs-only / docstring-only and contains no production code, no proof harness, no benchmark, no runtime behavior change.

## Release category

**Docs release.** The classification has zero items in `INCLUDE_RELEASE_CORE` and zero in `INCLUDE_RELEASE_TEST`. All three included items are documentation: two of them are docstrings inside operator-side tools, one is a session/release report.

## Tag decision (P0/P1 rule)

The master prompt is explicit:

> If the discovered work is docs-only, do not create a new prerelease tag. Create a docs PR only and write Decision B for tag.

→ **Tag decision: Decision B — no v3.10.3 tag created in this session.**

The default candidate tag `v3.10.3-local-delta-integration-alpha` and its alternatives (`v3.10.3-runtime-hardening-alpha`, etc.) are **not** appropriate here, because they would imply runtime/proof/benchmark changes that this session does not produce. Creating a prerelease tag against docs-only commits would inflate the release lineage with a tag whose claim surface is empty.

When future runtime / proof / benchmark work lands on `main`, that future session may pick up the `v3.10.3-*-alpha` candidate tag.

## Branch + worktree plan

* Base: `origin/main` at `1a51dcdbd51abfc3e64311bc20ea4eab2ebd987d`.
* Branch name: `phase18d/local-delta-docs`. (Factual engineering name; reflects "this PR carries forward local docs deltas not yet on GitHub". Avoids any runtime / hardening / dispatch wording that would mis-claim scope.)
* Worktree path: `C:\Python\project2-phase18d-local-delta-docs` (created via `git worktree add -B phase18d/local-delta-docs ../project2-phase18d-local-delta-docs origin/main`).
* Working directory for all P2-onward steps: that worktree.

## Files that will land in the PR

Exactly these 5 files (3 INCLUDE-classified deltas + 4 docs files this session is writing):

| Path | Origin | Action |
| --- | --- | --- |
| `tools/waggle_backup.py` | master worktree (M) | apply local v9.2 docstring edit |
| `tools/waggle_restore.py` | master worktree (M) | apply local v3.7.8.0 docstring edit |
| `docs/runs/phase18c_mined_solver_runtime_dispatch_2026_05_05/final_report.md` | phase18c worktree (untracked) | move untracked file into the new branch and commit |
| `docs/runs/phase18d_local_delta_release_2026_05_05/baseline_verification.md` | written this session | new |
| `docs/runs/phase18d_local_delta_release_2026_05_05/local_delta_inventory.md` | written this session | new |
| `docs/runs/phase18d_local_delta_release_2026_05_05/local_delta_classification.md` | written this session | new |
| `docs/runs/phase18d_local_delta_release_2026_05_05/local_delta_release_design.md` | this file | new |
| `docs/runs/phase18d_local_delta_release_2026_05_05/session_state.json` | written this session | new |
| `docs/runs/phase18d_local_delta_release_2026_05_05/host_verification.md` | will be written in P4 | new |
| `docs/runs/phase18d_local_delta_release_2026_05_05/release_decision.md` | will be written in P7 | new |
| `docs/runs/phase18d_local_delta_release_2026_05_05/pr_body.md` | will be written before P10 | new |
| `CHANGELOG.md` | edit on the new branch | append Phase 18D docs-only entry |
| `CURRENT_STATUS.md` | edit on the new branch | one-line note that v3.10.2 remains the most recent prerelease and Phase 18D is a docs-only PR (no new tag) |

The release-readiness doc (`docs/release/RELEASE_READINESS.md`) and `README.md` will **not** be edited in this PR because no new tag is being created, so the public release surface does not change. Editing them with a "no new tag" entry would clutter the release-readiness narrative.

The competitive evidence matrix (`docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md`) will **not** be edited because no axis advances; the release category is docs-only.

## Files explicitly NOT in the PR (parked / dropped)

* `WD_release_to_main_master_prompt.md` — operator notes, parked.
* `docs/atomic_flip_prep/03_HUMAN_APPROVAL.yaml` — DRAFT Stage-2 approval, parked per CLAUDE.md rule 10.
* `docs/runs/phase16g_post_reboot_handoff_2026_05_03/HANDOFF.md` — historical Phase 16G handoff, superseded, parked.
* `docs/runs/phase9_pr_body.md` — Phase 9 PR body draft, historical, parked.
* `.claude/` (lock + local settings) — dropped.
* `*.pid` files inside `/c/Python/project2` worktree — dropped.
* All `phase8.5/*` lineage untracked content (`/c/Python/project2`, `/c/Python/project2-d`) — out of release scope; phase8.5 is a separate parallel exploration track.
* Local merge commit `847e6bd` on `phase18c/mined-solver-runtime-dispatch` — dropped (obsoleted by squash-merge `e9aa1de1`).

## Test strategy

Because no production code or proof file changes, the targeted-suite obligation is light:

* Carry-forward sanity (P4):
  * `python -X utf8 -m pytest tests/phase10/ -q`
  * `python -X utf8 -m pytest tests/benchmarks/test_phase18a_benchmark_externalization.py -q`
  * `python -X utf8 tools/validate_phase18a_benchmark_bundle.py --bundle-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle`
  * `python -X utf8 tools/run_phase18b_gap_miner_feedback_proof.py --out-dir <tmp>`
  * `python -X utf8 tools/run_phase18c_mined_solver_runtime_dispatch_proof.py --out-dir <tmp>`
* No new Phase 18D test file is created (no code under test).
* No new Phase 18D proof harness is created.

The fresh-clone retest (P9) re-runs the same set inside a fresh `git clone`.

## Docker `--network none` strategy

Because no proof files change and no Dockerfile / .dockerignore change is required, Docker carry-forward only:

* `docker build -t waggledance:phase18d -f Dockerfile .`
* `docker run --rm --network none waggledance:phase18d python tools/validate_phase18a_benchmark_bundle.py --bundle-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle`
* `docker run --rm --network none waggledance:phase18d python tools/run_phase18b_gap_miner_feedback_proof.py --out-dir /tmp/phase18d_docker_phase18b`
* `docker run --rm --network none waggledance:phase18d python tools/run_phase18c_mined_solver_runtime_dispatch_proof.py --out-dir /tmp/phase18d_docker_phase18c`

If host carry-forward already passes, Docker carry-forward is the offline-equivalent re-run. No new Phase 18D Docker invocation is needed.

## Secret-hygiene strategy

Per the absolute-secret-hygiene block of the master prompt:

* Never run `gh auth token`, `gh auth git-credential get`, or any command that prints `password=...`.
* Never embed a token in a remote URL, branch upstream URL, command line, log, doc, release note, or PR body.
* Use only `gh auth status`, `gh auth setup-git` (if needed), plain `git push -u origin <branch>`, `gh pr create`, `gh pr merge`, `gh release create`.
* Before commit and before push, run a secret-pattern grep that reports only file paths and redacted pattern names (no secret values). Patterns: `gho_[A-Za-z0-9_]{20,}`, `github_pat_[A-Za-z0-9_]{20,}`, `https://x-access-token:[^@\s]+@`, `Authorization: Bearer [A-Za-z0-9_.-]{20,}`, `BEGIN PRIVATE KEY`, `BEGIN RSA PRIVATE KEY`.
* Before push, verify `git remote -v` is exactly `https://github.com/Ahkeratmehilaiset/waggledance-swarm.git` and contains no `x-access-token`, `gho_`, or `github_pat_` substring.

The previously-leaked `gho_*` token from the Phase 18C session was rotated by the operator before Phase 18D began. Phase 18D operates only via the Windows Credential Manager / `gh` credential helper.

## Rollback strategy

* If any P4/P5/P9 gate fails, do not push. Hold the branch locally; switch to Decision B in `release_decision.md`; do not open a PR.
* If the PR opens but CI fails, do not merge; fix on the branch (still docs-only) or close the PR.
* If the PR is merged but a regression is observed in carry-forward proofs (extremely unlikely for a docs-only PR), open a follow-up revert PR; do not move any tag.

## Allowed / forbidden claims

**Allowed claims** (all must be backed by this session's artifacts):

* Phase 18D is a docs-only PR carrying forward 1 release-audit document and 2 operator-tool docstring updates.
* All 8 prior tag SHAs (`v3.8.0` through `v3.10.2-mined-solver-dispatch-alpha`) are unchanged.
* `v3.8.0` remains GitHub Latest.
* No model pull/download, no cloud API call, no live builder execution.
* No Stage-2 atomic flip, no HUMAN_APPROVAL collected.
* No allowlist widening, no new high-risk autonomy mechanism.
* No new pip dependency.
* No DB / SQLite / WAL / SHM file committed.
* No token or secret committed in any repo file, PR body, release note, or branch upstream URL.

**Forbidden claims** (must NOT appear anywhere in PR body, docs, commits, or release notes):

* No raw-intelligence superiority claim.
* No cross-vendor ranking claim.
* No "beats all competitors", "world fastest", "world best" or comparable language.
* No consciousness, sentience, awareness, or AGI claim.
* No claim of a new prerelease tag — the PR explicitly does not create one.
* No claim that "no token was ever exposed" — the operator's prior session did briefly expose a token in local command output. The truthful wording (per master prompt P6) is:

  > "No token or secret was committed to repository files, release artifacts, tags, PR bodies, or GitHub releases. A prior token exposure occurred in local/session command output before Phase 18D and was remediated by operator token rotation. Phase 18D uses credential-helper Git/GitHub operations and does not print or embed tokens."

## Stop / abort triggers (Decision B)

Phase 18D will switch to Decision B (no PR, or PR but no tag — already the plan) and stop fail-closed if any of the following occurs:

* a real secret-shaped string is found inside any INCLUDE-classified file;
* any of the 8 prior tags moves;
* `v3.8.0` is no longer GitHub Latest;
* P4 host carry-forward proofs fail;
* P5 Docker `--network none` carry-forward proofs fail;
* P9 fresh-clone retest fails;
* PR-level CI fails;
* the user instructs Phase 18D to stop.
