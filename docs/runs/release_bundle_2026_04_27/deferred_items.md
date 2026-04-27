# Deferred Items (v3.6.0)

Things explicitly NOT shipped in this release. All have a documented landing path.

## Tier 1 — Follow-up PRs after this release lands on main

| Item | Branch | Substantive commits | Spec |
|---|---|---|---|
| R7.5 — Vector Writer Resilience | `phase8.5/vector-chaos` | 4 | `docs/architecture/VECTOR_WRITER_RESILIENCE.md` |
| Session A — Curiosity Organ (gap_miner) | `phase8.5/curiosity-organ` | 10 | `docs/architecture/GAP_MINER_VISION.md` |
| Session B — Self-Model Layer | `phase8.5/self-model-layer` | 16 | `docs/architecture/SELF_MODEL_LAYER.md` + formulas/provenance docs |
| Session C — Dream Pipeline | `phase8.5/dream-curriculum` | 22 | `docs/architecture/DREAM_MODE_2_0.md` + formulas/provenance docs |
| Session D — The Hive Proposes | `phase8.5/hive-proposes` | 21 | `docs/architecture/THE_HIVE_PROPOSES.md` + formulas/provenance docs |

Each has a producer subsystem and existing tests; each rebases on the post-Phase-9 main and ships independently. Phase 9 already contains the IR adapter contracts (`from_curiosity`, `from_self_model`, `from_dream`, `from_hive`); the producers ship separately without breaking Phase 9 invariants.

## Tier 2 — Atomic runtime flip

The actual repointing of the live runtime read path to the new fabric is a separate, explicitly human-gated operation:

- Spec: `docs/architecture/PROMPT_2_INPUTS_AND_CONTRACTS.md`
- Preparation materials: `docs/atomic_flip_prep/00_README.md` through `06_rollback_procedure.md`
- Operates from an isolated worktree (`/c/python/project2-flip`)
- Gated on a signed approval artifact (`HUMAN_APPROVAL.yaml`)
- Cannot run until 400h gauntlet campaign is finished or frozen

## Tier 3 — Phase 12+ deferred high-risk variants

Documented in `docs/architecture/HIGH_RISK_VARIANTS_DEFERRED.md` with explicit blockers per item:

1. **Speculative parallel provider ensembles** — blocked on per-task budget caps, provenance retention, profile-flag default-off
2. **Predictive cache preheating** — blocked on per-window speculative cap, predictor accuracy measurement, consultation-log tagging
3. **Unbounded micro-learning expansions** — blocked on hard-rule violation proof, byte-stable rollback, per-tick volume cap
4. **Limited canary auto-promotion under experimental profile** — blocked on opt-in profile, fixed allow-list, aggregate canary metrics gate
5. **Advanced local model escalation** — blocked on per-domain corpus pinning, drift detector at nominal for fixed window, human review per task_kind expansion
6. **Generative memory compression** — explicitly Phase 12+; blocked on two-tier storage with retention proofs, byte-stable round-trip tests

## Tier 4 — Operationally lower-priority

- README Phase 8 description has been preserved verbatim (just contextualized as "the substrate Phase 9 builds on"). A future minor doc PR could trim it further if desired.
- The two-entry-point Docker reality (`start_waggledance.py` vs `start_runtime`) is documented but not unified. A future operational PR could consolidate.
- The full ~5817-test suite was not run in the release session per multi-session prompt safety rule. CI runs it on every push; the release branch is fully green there.

## Why deferral is responsible

Per WD_release_to_main_master_prompt.md Strategy A:

> do NOT create extra release complexity unless required

All Tier 1 items can be cleanly rebased on a post-Phase-9 main. None of them requires Phase 9 modifications to land. The right-sized release is "Phase 9 scaffold ships now; producers ship next."

Tier 2 (atomic flip) is a different risk domain by explicit design. Combining the scaffold release with the runtime flip in one session would conflate two reviewable concerns.

Tier 3 is intentionally many sessions away. Each item is documented as "what would have to be true before this is even discussable."

Tier 4 is operational hygiene that a future minor PR can address without affecting v3.6.0's correctness.
