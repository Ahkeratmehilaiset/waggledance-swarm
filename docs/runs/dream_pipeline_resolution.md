# Phase 2 — Dream Pipeline Resolution

## Decision

**Option 3: Explicitly deferred to phase8.5/dream-curriculum follow-up PR.**

The Phase 9 release ships the dream-pipeline **contract surface** but
NOT the new dream-curriculum producer scaffold. The producer code
lands as a separate PR after Phase 9 merges to main.

## What is in Phase 9 (re: dream)

- `waggledance/core/ir/adapters/from_dream.py` — IR adapter that
  defines how dream-generated artifacts get translated into the
  Cognition IR shared with the rest of the autonomy fabric. This is
  the formal contract Phase 9 owns.
- `waggledance/core/learning/dream_mode.py` — pre-existing Phase 8
  dream mode module (additive scaffolding, was already on phase8
  base before Phase 9 forked).
- `waggledance/core/proposal_compiler/` — consumes meta-proposals
  that originate (in production) from dream-driven sessions; the
  compiler itself is producer-agnostic.

## What is NOT in Phase 9 (intentionally deferred)

These commits on `phase8.5/dream-curriculum` are out of scope for
the Phase 9 release:

- `5a06008` feat(phase8.5): add dream curriculum planner + 18 tests
- `152a8bc` feat(phase8.5): add dream request packs and deterministic proposal ingestion
- `57f1c89` feat(phase8.5): add shadow graph and replay harness
- `d297cdc` feat(phase8.5): add dream meta-proposal artifacts
- `ea56456` docs(phase8.5): document Dream Mode 2.0, replay formulas, provenance, and meta-learner hooks
- `623e90c` chore(phase8.5): close Session C — verify Change Date metadata
- `fe7bde4` verify(phase8.5): execute dream pipeline end-to-end on real Session A+B data

## Why this is safe

- **Phase 9 does not run dream pipelines.** Nothing on
  phase9/autonomy-fabric imports or invokes dream-curriculum
  producers. Verified by source-grep: no `from waggledance.core
  .dreaming` or `from .dream_curriculum` import anywhere in Phase 9
  modules.
- **Phase 9's evidence artifact** at
  `docs/runs/phase9_pipeline_demo_compiled/` shows the proposal
  compiler accepting a real meta-proposal output from
  `phase8.5/hive-proposes` (which itself is downstream of dream
  outputs). The compiler ran successfully against real artifacts
  produced on the dream-curriculum worktree, but the Phase 9 commit
  contains only the resulting JSON bundle, not the source code that
  produced the input.
- **The IR contract** in `from_dream.py` is fully tested and
  unchanged from how `phase8.5/dream-curriculum` expects it. When
  the dream-curriculum PR merges later, no Phase 9-side change is
  required.
- **Strategy A minimal-scope rule:** integrating the dream-curriculum
  scaffold now would more than double the PR diff and pull in 22
  commits worth of producer code that doesn't add Phase 9 release
  value.

## Acceptance criterion compliance

Per the master prompt §PHASE 2: "Do NOT leave this ambiguous." This
document removes the ambiguity:

- Status: **DEFERRED**
- Blocker for Phase 9 release: **NONE**
- Reason release remains valid: Phase 9 ships the IR contract for
  dreams; the producer can land as a separate PR without breaking
  Phase 9's invariants.

## Follow-up

When `phase8.5/dream-curriculum` is rebased on the post-Phase-9 main
and submitted as its own PR, the dream pipeline producer code will
land there. Until then, Phase 9 main has the contract but no live
producer; this is identical to how Phase 9 has lived under review.
