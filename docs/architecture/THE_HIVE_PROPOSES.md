# The Hive Proposes

Phase 8.5 Session D, crown-jewel area `waggledance/core/meta/*`.

WD's first bounded self-proposal layer. The hive proposes; humans
decide.

## What this is

A deterministic, offline, shadow-only consumer of upstream session
outputs. It aggregates evidence across up to four planes (curiosity,
self-model, dream, resilience), scores candidate self-proposals, and
emits a machine-readable + human-readable review handoff.

## What this is NOT

- not self-rewrite
- not automatic merge / apply
- not runtime mutation
- not a code generator
- not a substitute for human review
- not authoritative — humans are

## Layered architecture

```
pinned upstream artifacts
        │  (Session A curiosity + Session B self-model +
        │   Session C dream_meta_proposal + optional R7.5 resilience)
        ▼
inputs.py (bounded read, hook contract validation)
        │
        ▼
meta_learner.gather_*_evidence  →  list[EvidenceItem]
        │
        ▼
meta_learner.aggregate_by_target  →  dict[canonical_target, items]
        │
        ▼
meta_learner.synthesize_proposals
        │  - proposal_type inference (D.txt §D4 trigger rules)
        │  - multi-plane requirement enforcement
        │  - proposal_priority + confidence scoring (D.txt §D5)
        │  - meta_proposal_id (sha256 of structural identity)
        │  - lifecycle_for(history)
        ▼
SynthesisResult{proposals, insufficient_evidence, rejected}
        │
        ▼
review_bundle.recommend_action_for  →  one of 4 enum actions
review_bundle.build_review_bundle    →  curated reviewer summary
review_bundle.emit_*                  →  hive_proposals.{json,md},
                                          meta_evidence_map.json,
                                          review_bundle.{json,md}
history.append_entry                  →  HISTORY.jsonl chain
```

## Crown-jewel safety

| Property | Mechanism |
|---|---|
| no runtime mutation | meta package never imports runtime registries / FAISS / port 8002; tests assert this |
| no axiom write | source greps forbid `axiom_write(`, `register_solver_in_runtime(`, `merge_proposal_now(`, `promote_to_runtime(` |
| no live LLM | source greps forbid `requests.post(`, `httpx.post(`, `openai.`, `anthropic.`, `ollama.` |
| no_mutation_in_session = true | every emitted MetaProposal carries this const-true field; source greps forbid the false assignment |
| BUSL Change Date 2030-03-19 | already set in LICENSE-BUSL.txt from Session B commit e3479dd |

## Determinism contract

- canonical JSON serialization (sorted keys, indent=2 for human
  readability, trailing newline)
- evidence aggregation ordered by stable keys (plane, source_id)
- proposal ranking by `(-priority, meta_proposal_id)`
- meta_proposal_id excludes volatile evidence refs
- HISTORY.jsonl entries are byte-stable across re-runs of the same
  pinned input set (ts is supplied externally; a future-clock-free
  replay can pin ts to the run-start UTC second)

Byte-identical determinism is enforced by tests #44–#48 (D.txt
§TESTING REQUIREMENTS).

## Two-file review handoff

`hive_proposals.json` — full machine-readable records (one per
proposal). Validates against `schemas/meta_proposal.schema.json`.

`review_bundle.json` — curated reviewer summary; references proposals
by `meta_proposal_id`; carries reviewer context:
- `recommended_next_human_action`
- `insufficient_evidence[]` (weak-evidence candidates)
- `rejected_candidates[]`
- `why_human_review_required`
- `why_no_runtime_mutation_occurred`
- `fixture_fallback_used` (per-plane status)

The two files together form the complete handoff. Validates against
`schemas/review_bundle.schema.json`.

## Proposal types

| type | scope_class | typical evidence |
|---|---|---|
| topology_subdivision | topology | dream subdivision hint + ≥2 primary planes |
| solver_family_growth | solver_library | dream + (curiosity ∨ self_model) |
| solver_family_consolidation | solver_library | self_model + dream redundancy |
| policy_gate_adjustment | policy | recurring calibration_oscillation |
| introspection_gap | introspection | strong self-model alone is allowed |
| archival_cleanup | archival | rarely emitted; future use |
| infrastructure_followup | infrastructure | R7.5 best_effort row alone is allowed |

## Recommended next human actions

| action | when |
|---|---|
| `post_campaign_runtime_review_candidate` | confidence ≥ 0.6, priority ≥ 0.05, scope_class ∈ {topology, solver_library, policy} |
| `review_for_future_PR` | confidence ≥ 0.4, priority ≥ 0.02 |
| `archive_as_low_value` | confidence < 0.3 and priority < 0.01 |
| `wait_for_more_evidence` | otherwise |

`post_campaign_runtime_review_candidate` is **advisory only**. It
marks a proposal as *eligible* for a future Phase 9 runtime-flip
review session. It does not authorize merge.

## Why this is safe during the live campaign

- offline-only: zero network calls, zero LLM dependencies
- read-only on upstream artifacts (pinned size_bytes ceiling)
- writes only to `docs/runs/hive/<sha12>/` and `HISTORY.jsonl`
- worktree-isolated: lives at `C:/python/project2-d`; the live
  gauntlet at `C:/python/project2/` is untouched
- no port binding, no daemon, no background process
