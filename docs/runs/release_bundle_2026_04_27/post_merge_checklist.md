# Post-Merge Checklist (after Phase 9 lands on main)

Reachable once the operator has executed the merge per `merge_readiness_checklist.md`.

## Immediate (within 1 hour of merge)

- [ ] `git fetch origin` and verify `origin/main` tip equals the squash-merge commit
- [ ] Run `pytest tests/test_phase9_*.py -q` against `main` checkout — confirm 657 passed
- [ ] CI on main runs and reports green
- [ ] `gh pr view 51 --json state` returns MERGED
- [ ] Tag `v3.6.0` is created and pushed
- [ ] GitHub release `v3.6.0` is published with `release_notes_draft.md` body
- [ ] Update `docs/runs/release_to_main_state.json`:
  - `merge_performed: true`
  - `merged_commit_sha: <40-char>`
  - `tag_performed: true`
  - `tag: "v3.6.0"`

## Same day

- [ ] Update CHANGELOG.md — add a `## Released — 2026-04-27 — v3.6.0` line at the top of the [3.6.0] entry
- [ ] Open follow-up PR for Phase 8.5/vector-chaos (R7.5) — first in the dependency chain
- [ ] Update CURRENT_STATUS.md to reflect v3.6.0 release

## Within a week

- [ ] Phase 8.5 follow-up PRs in order:
  - `phase8.5/vector-chaos → main`
  - `phase8.5/curiosity-organ → main` (after vector-chaos lands; rebase on new main)
  - `phase8.5/self-model-layer → main` (after curiosity-organ; rebase)
  - `phase8.5/dream-curriculum → main` (after self-model-layer; rebase)
  - `phase8.5/hive-proposes → main` (after dream-curriculum; rebase)

Each PR uses `tools/wd_pr_prepare.py` (added on phase8.5/dream-curriculum, will be on main after that branch's PR merges).

## Before Prompt 2 (atomic flip)

Prerequisites that must all be true before scheduling the Prompt 2 session:

- [ ] All five Phase 8.5 follow-up PRs are merged on main
- [ ] 400h gauntlet campaign is finished or frozen (no auto-commit pid files in `docs/runs/ui_gauntlet_400h_*/`)
- [ ] Operator has authored and signed `HUMAN_APPROVAL.yaml` per `docs/atomic_flip_prep/03_HUMAN_APPROVAL.yaml.draft`
- [ ] Approval artifact's sha256 is pinned somewhere reproducible
- [ ] All pre-flip checklist items in `docs/atomic_flip_prep/02_pre_flip_checklist.md` are checked

## What this release does NOT trigger

- Does not start any background daemon
- Does not invoke any provider or LLM
- Does not modify any production database or runtime state
- Does not affect the live 400h gauntlet campaign currently running on phase8.5/dream-curriculum

The merge is a pure version control operation. The runtime continues to behave exactly as it did before the merge — Phase 9 is observable but not authoritative until Prompt 2 runs.

## Communicating the release

Suggested release announcement template:

> WaggleDance v3.6.0 — Phase 9 Autonomy Fabric scaffold released.
>
> 16 phases of cognitive-OS architecture: always-on kernel, cognition IR, vector identity, world model, builder/mentor lanes, multi-provider plane with 6-layer trust gate, autonomous solver synthesis with cold-shadow throttling, memory tiers with pinning, real hex runtime topology, 14-stage promotion ladder with human gate, proposal compiler, local-model safe scaffold, cross-capsule observer.
>
> Review-only release. The atomic runtime flip is a separate session, gated on a signed human approval artifact. Existing 3.5.7 runtime path remains live until then.
>
> Full release notes: <gh release URL>
> Changelog: CHANGELOG.md
> Roadmap: docs/architecture/PHASE_9_ROADMAP.md
