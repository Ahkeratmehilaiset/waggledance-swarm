# Phase 8.5 Local-Only Branch Audit (Phase 1)

Performed under WD_release_to_main_master_prompt.md, Strategy A.

## Summary

All five Phase 8.5 branches contain substantive work NOT in
`phase9/autonomy-fabric`, but **all five are deferred to follow-up PRs
after the Phase 9 release lands on main**. The Phase 9 release is a
self-contained scaffold (autonomy kernel, IR, capsules, world model,
provider plane, builder lane, solver synthesis, memory tiers, hex
runtime topology, promotion ladder, proposal compiler, local
intelligence scaffold, cross-capsule observer) and its merge does
NOT require any phase8.5 subsystem.

## Audit table

| Branch | Substantive (non-campaign) commits not in phase9 | Status | Reason |
|---|---|---|---|
| `phase8.5/vector-chaos` | 4 | **DEFER** | R7.5 vector-writer resilience tests + envelope docs. Independent post-Phase-8 hardening; not in Phase 9 contract surface. Ships as separate PR after Phase 9 lands on main. |
| `phase8.5/curiosity-organ` | 10 | **DEFER** | Session A `gap_miner` core + R7.5 spec + tests + real-data outputs. Standalone curiosity-mining subsystem; consumed BY Phase 9 IR adapter (`from_curiosity.py` already in phase9), but the producer code itself can ship separately. |
| `phase8.5/self-model-layer` | 16 | **DEFER** | Session B `self_model_snapshot` core + reflective workspace + tensions + calibration corrections + history chain + invariants. Phase 9 includes the IR adapter `from_self_model.py` already. The producer scaffold can ship as a separate PR. |
| `phase8.5/dream-curriculum` | 22 | **DEFER** | Session C dream curriculum + replay harness + meta-proposal + dream pipeline end-to-end real-data run. Plus 3 release-helper commits added 2026-04-26: `0624a9f` wd_pr_prepare.py tool, `39602df` CLAUDE.md expansion, `bfa526a` journal. None blocks Phase 9 merge; all ride on a follow-up PR. |
| `phase8.5/hive-proposes` | 21 | **DEFER** | Session D meta_learner + hive_proposes CLI + history chain + review bundle. Phase 9 includes `from_hive.py` IR adapter. The producer ships separately. |

**Total deferred substantive commits across all 5 branches: 73.**

## Why this is safe

- **Phase 9 PR is self-contained:** phase9/autonomy-fabric tip
  `3c41a5c` carries 105 commits ahead of main and 0 behind. All 16
  Phase 9 phases (F–Q) are present, all 657 targeted tests pass, all
  5 CI checks are green.
- **Phase 9 includes the IR adapters** (`from_curiosity.py`,
  `from_self_model.py`, `from_dream.py`, `from_hive.py`) under
  `waggledance/core/ir/adapters/`. The adapters define the contract
  between Phase 8.5 producers and Phase 9 consumers. Since the
  contract lives in Phase 9, downstream Phase 8.5 PRs can land
  against an already-merged Phase 9 main without choreography.
- **The 4 evidence artifacts** committed in Phase 9
  (`phase9_reality_view_render.json`,
  `phase9_pipeline_demo_compiled/`, `phase9_kernel_tick_dry_run.json`,
  `phase9_conversation_probe.json`) are JSON outputs baked into the
  Phase 9 commit. They demonstrate the Phase 9 contract works against
  real Session B/D data, but the Session B/D *producers* don't need
  to be on main for those JSON evidence files to remain valid.
- **No code on phase9/autonomy-fabric imports** any Phase 8.5
  producer module (verified by `git diff main..phase9` — no imports
  of `from waggledance.core.curiosity` / `self_model_snapshot` /
  `dream_curriculum` / `hive_proposes`).
- **Strategy A directive:** "do NOT create unnecessary new PR stacks
  if the missing local-only work can be integrated safely into the
  existing Phase 9 release branch" — but it ALSO says
  "do not opportunistically merge unrelated experiments". Phase 8.5
  subsystems are functionally separate ship-units. Deferring is the
  surgical choice.

## Follow-up PR sequence (after Phase 9 lands on main)

In recommended order (each rebases onto previous main tip):

1. **PR #N+1**: `phase8.5/vector-chaos → main` (R7.5 — Vector Writer Resilience)
2. **PR #N+2**: `phase8.5/curiosity-organ → main` (Session A — Curiosity Organ)
3. **PR #N+3**: `phase8.5/self-model-layer → main` (Session B — Self-Model Layer; depends on A's gap_miner being merged)
4. **PR #N+4**: `phase8.5/dream-curriculum → main` (Session C — Dream Pipeline; depends on B)
5. **PR #N+5**: `phase8.5/hive-proposes → main` (Session D — The Hive Proposes; depends on C)

Once #N+5 merges, the system has end-to-end real-data path:
ingest → curiosity → self-model → dream → hive → Phase 9 cognition IR → proposal compiler → human review → atomic flip (Prompt 2, separate session).

## Specific commits flagged as "useful but deferred"

These three commits on `phase8.5/dream-curriculum` are operationally
relevant to Phase 9 release (`wd_pr_prepare` tool was built FOR this
very release process; CLAUDE.md update describes Phase 8.5/9 rules;
journal documents yesterday's session). But cherry-picking them now
would ADD scope to a release that's already mergeable. Per Strategy A
minimal-scope rule, they ride on the dream-curriculum follow-up PR:

- `0624a9f` `feat(tools): add wd_pr_prepare.py for automated PR preparation`
- `39602df` `docs(CLAUDE.md): expand operator rules with Phase 8.5/9 context`
- `bfa526a` `docs(journal): record CLAUDE.md acceptance and git identity fix`
