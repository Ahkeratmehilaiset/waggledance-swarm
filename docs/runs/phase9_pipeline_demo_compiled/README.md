# Phase 9 — End-to-End Pipeline Demo (Session D meta-proposal → Phase O bundle)

This directory is committed evidence for **Master Acceptance Criterion
#14**: "WD has a real path from ingest → reflection → dream → synthesis
→ proposal → review."

## Pipeline traversed

1. **Session D — `the_hive_proposes`** authored
   `C:/python/project2-d/docs/runs/hive/f3acc2a985b8/hive_proposals.json`
   (3 meta-proposals; pinned-input manifest sha256
   `f3acc2a985b819b6b5ef985a4bf5528e3988ae5112dfca36e60d5878dcb17d2e`).
2. **Extraction**: the first proposal (`meta_proposal_id=4116420fed0a`,
   `proposal_type=introspection_gap`, `canonical_target=63f8a14965db`,
   `confidence=0.6`) was extracted to
   `docs/runs/phase9_pipeline_demo_input_meta_proposal.json` so the
   Phase O compiler could consume it as a single object.
3. **Phase O — Proposal Compiler**:
   ```
   python tools/compile_meta_proposal.py \
     --meta-proposal-path docs/runs/phase9_pipeline_demo_input_meta_proposal.json \
     --output-dir docs/runs/phase9_pipeline_demo_compiled \
     --apply --json
   ```
   produced `bundle_id=bundle_9b273467f0` containing 4 artifacts:

| File | Role |
|---|---|
| `bundle.json` | Full ProposalBundle JSON with const-true `no_main_branch_auto_merge` + `no_runtime_mutation` |
| `pr_draft.md` | Reviewer-facing PR draft (not auto-pushed) |
| `patch_skeleton.diff` | Marked-as-skeleton placeholder; a human reviewer or Phase U2 builder lane fills concrete diffs |
| `review_checklist.md` | 8-item human reviewer gate including hook-contract re-hashing |

## What this demonstrates

- **Determinism**: rerun produces the same `bundle_id`.
- **Provenance preserved**: the compiled bundle records the source
  meta-proposal id `4116420fed0a` and the upstream Session D
  pinned-input manifest sha256 in the input artifact's
  `provenance.pinned_input_manifest_sha256`.
- **Never auto-applies**: the patch is explicitly a SKELETON (header
  literally says so); the rollout plan stages are
  `human_review → post_campaign_runtime_candidate → review_only`,
  not direct main merge.
- **Cross-session contracts respected**: the input meta-proposal
  carries `consumed_hook_contracts[]` referencing
  `HOOKS_FOR_DREAM_CURRICULUM.md` v1 with its full sha256 — exactly
  the hook-contract pattern documented in CLAUDE.md.

## Why only 1 of 3 proposals was compiled

The compiler signature accepts a single proposal per invocation. To
keep the demo a clean acceptance evidence (rather than a dump), only
the first proposal was compiled. The other two could be compiled
identically; the same determinism guarantee applies.

## Reviewer next step

If a human reviewer wishes to push this bundle further, follow
`docs/architecture/PROMPT_2_INPUTS_AND_CONTRACTS.md`. The bundle here
is shadow-only; no runtime, no main-branch merge.
