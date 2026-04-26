# Hooks: hive_proposes → runtime review

`contract_version: 1`

This contract describes how a future Phase 9 runtime-flip review
session may consume Session D's `review_bundle.json` and
`hive_proposals.json` artifacts. It is consumer-only — nothing in
this contract authorizes runtime mutation, axiom write, or live
solver registration during Session D.

## Inputs

A consumer of this contract must read:

- `docs/runs/hive/<sha12>/review_bundle.json`
  (validates against `schemas/review_bundle.schema.json`)
- `docs/runs/hive/<sha12>/hive_proposals.json`
  (each entry validates against `schemas/meta_proposal.schema.json`)
- `docs/runs/hive/<sha12>/meta_evidence_map.json`
  (per-target evidence breakdown; informational only)
- `docs/runs/hive/HISTORY.jsonl`
  (chained, append-only; consumer should `validate_chain` before
  trusting lifecycle status)

## Required validations before consumer acts

1. Re-hash `consumed_hook_contracts[]` from `review_bundle.json`
   against on-disk files. Reject the bundle on any mismatch.
2. Verify `pinned_input_manifest_sha256` matches an authentic
   pinning event in repository history.
3. Walk `HISTORY.jsonl`'s chain. Reject the bundle if
   `validate_chain` reports a break.
4. Verify every proposal's `no_mutation_in_session == true`. The
   absence of this field, or a `false` value, indicates a forged or
   off-contract bundle.

## semantics — `proposal_priority`

Unclamped ranking score, value range `[0, 2.10]` in practice. **Not
a confidence score.** Consumers must NOT interpret a high priority
as authorization to merge — it is only a sort key.

## semantics — `confidence`

Clamped to `[0, 1]`. Components per D.txt §D5:
- 0.40 if any primary plane supports
- 0.20 if a second primary plane supports
- 0.20 if dream replay shows positive structural gain
- 0.20 if no major contradiction across planes

A consumer that promotes a proposal must require `confidence ≥ 0.80`
or have additional human-approved evidence beyond what the bundle
records.

## semantics — `why_human_review_required`

Free-form English string. Consumers must surface this verbatim to
the human reviewer. Do NOT machine-parse it as authorization.

## semantics — `post_campaign_runtime_review_candidate`

A **marker, not an authorization**. It indicates the proposal cleared
the recommend_action_for thresholds (confidence ≥ 0.6, priority ≥
0.05, scope_class ∈ {topology, solver_library, policy}). The Phase 9
review session must apply its own merge criteria on top.

A Phase 9 consumer may NOT:
- merge a proposal solely because it carries this marker
- skip human review when the marker is present
- promote a proposal whose `lifecycle_status == "resolved"` (the
  bundle has already concluded that proposal is no longer relevant)

A Phase 9 consumer MAY:
- treat the marker as a *prefilter* that limits which proposals enter
  human review
- reorder its review queue by `proposal_priority`
- combine bundle evidence with a fresh structural counterfactual
  replay (e.g. C.4's shadow_replay_report.json) before recommending
  merge to a human

## Forbidden actions for any consumer of this contract

- promoting a proposal to live runtime without explicit human
  approval recorded outside this bundle
- writing axiom YAML based on proposal contents
- writing FAISS indexes based on proposal contents
- mutating any file under `waggledance/core/dreaming/*`,
  `waggledance/core/meta/*`, or `waggledance/core/magma/self_model*`
  without updating the BUSL Change Date in the same commit
  (currently `2030-03-19`)
- skipping the chain integrity check on `HISTORY.jsonl`

## Future contract evolution

If a later session adds new fields to `review_bundle.json` or
`hive_proposals.json`, the contract version must bump to 2 and the
delta must be documented at the top of this file. Old v1 consumers
must continue to work by ignoring unknown additive fields.

If a future session changes the meaning of an existing field, that
is a **breaking change** and requires a major version bump plus a
migration note.

## Boundary — Session D vs Phase 9

Session D defines:
- input format
- evidence aggregation rules
- scoring formulas
- recommendation classification
- chain integrity rules

Session D does NOT define:
- merge criteria
- runtime promotion thresholds
- rollback policy
- human review interface
- approval workflow

Those are Phase 9's responsibility, working from this contract.
