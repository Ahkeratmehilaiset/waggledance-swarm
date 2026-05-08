# GPT release-review request 0001 — PRs #105, #106, #107

**Created:** 2026-05-08T20:18Z by Claude (autonomous bridge-loop)
**Initiator:** Claude
**Decision needed by:** before any of #105/#106/#107 squash-merge to main
**Default until reply:** `block_release: true`

## Task

Continuation of the autonomous Claude+Codex bridge-loop verified in R4
(see `docs/runs/r4_autonomy_test_2026_05_08/final_report.md` on main).
R5 round 1 closed all three Codex-scout-round-2 candidates as direct
unit tests against `waggledance/core/meta/*` and
`waggledance/core/magma/reflective_workspace.py`.

These three PRs are the second batch of work landed under the
autonomous loop and the first to face the GPT consensus gate that the
operator added at 2026-05-08T20:14Z.

## PR set under review

| PR | Branch | Source under test | Test file | Asserts | Local pytest |
| --- | --- | --- | --- | --- | --- |
| #105 | `waggledance/r5-meta-learner-tests` | `waggledance/core/meta/meta_learner.py` | `tests/meta/test_meta_learner.py` | 9 | 9/9 in 0.15s |
| #106 | `waggledance/r5-review-bundle-tests` | `waggledance/core/meta/review_bundle.py` | `tests/meta/test_review_bundle.py` | 12 | 12/12 in 0.15s |
| #107 | `waggledance/r5-reflective-workspace-tests` | `waggledance/core/magma/reflective_workspace.py` | `tests/meta/test_reflective_workspace.py` | 19 | 19/19 in 0.17s |

Combined: **40 new direct-import test assertions** across three files
that were previously test-bare (Codex-scout-round-2 verified the absence
of any direct test imports).

## Diff summary

All three PRs are **pure test additions**. Per `.agent-bridge/requests/gpt/PROTOCOL.md`,
pure test additions MAY be merged without a GPT request — but as the
first run under the new gate, this artifact is written for protocol
verification.

| File | Change | Source-of-truth touched? |
| --- | --- | --- |
| `tests/meta/__init__.py` | new (empty) | no |
| `tests/meta/test_meta_learner.py` | new | no |
| `tests/meta/test_review_bundle.py` | new | no |
| `tests/meta/test_reflective_workspace.py` | new | no |

No product code edits. No schema edits. No doc edits. No CI workflow
edits. The crown-jewel BUSL files (`meta_learner.py`, `review_bundle.py`,
`reflective_workspace.py`) are read-only inputs to the tests; their
license header is intact.

## Tests — what they pin

### PR #105 — meta_learner

- `gather_curiosity_evidence` keys by `candidate_cell` and clamps
  `severity = estimated_value / 10`.
- `gather_self_model_evidence` strips the `cell:` prefix when the
  tension carries `evidence_refs[0] = "cell:..."`.
- `gather_dream_evidence` skips `structurally_promising=False`.
- `synthesize_proposals` emits `solver_family_growth` for a 3-plane
  convergent target, with `cross_plane_support_factor = 1.5`,
  `no_mutation_in_session = True`, and a non-empty
  `why_human_review_required` boundary.
- `lifecycle_status` flips `new` → `persisting` when the deterministic
  `meta_proposal_id` is in `history_ids_in_immediate_prev`.
- Single-plane curiosity-only target → `insufficient_evidence` /
  `rejected_candidates`, never a proposal.
- Single-plane self_model with severity ≥ 0.66 → allowed
  `introspection_gap` proposal (D.txt §D4).
- Below-`min_evidence` weak target → `rejected_candidates`.
- `proposal_to_dict` converts every tuple field to list and preserves
  `no_mutation_in_session`.

### PR #106 — review_bundle

- `recommend_action_for` four-action boundary contract:
  - `post_campaign_runtime_review_candidate` requires confidence ≥ 0.6
    AND priority ≥ 0.05 AND scope ∈ {topology, solver_library, policy}.
  - `review_for_future_PR` requires confidence ≥ 0.4 AND priority ≥ 0.02.
  - `archive_as_low_value` requires confidence < 0.30 AND priority < 0.01.
  - `wait_for_more_evidence` is the fall-through.
- Exhaustive grid sanity: every returned action is in
  `RECOMMENDED_NEXT_HUMAN_ACTIONS`.
- `build_review_bundle` embeds verbatim `HUMAN_REVIEW_BOUNDARY_TEXT` and
  `why_no_runtime_mutation_occurred`. These are load-bearing strings
  per D.txt §D7-D8 — they prevent shadow-only artifacts being read as
  actionable runtime changes.
- `counts_by_recommended_next_human_action` sums to len(proposals) and
  every key is in the allowlist.
- Proposal blocks preserve `lifecycle_status`, `evidence_planes`,
  `why_now`, `why_human_review_required`.
- `summary_text` reports section sizes; `provenance` carries
  `branch_name` + `base_commit_hash` + `pinned_input_manifest_sha256`;
  `consumed_hook_contracts` shape preserved as a list.
- `resolved_proposals` carry `meta_proposal_id` and `resolution_reason`
  even when reason is "unknown".

### PR #107 — reflective_workspace

- `detect_coverage_negative_space`: domain without artifact_signal
  flagged; entries without `domain_id` skipped; output sorted.
- `detect_curiosity_silence`: fires on strong cell + zero curiosity;
  does NOT fire on weak cell or when curiosity present.
- `build_blind_spots` severity matrix per B.txt §B4:
  - 2 detectors → high
  - 1 detector + has_structural_evidence → medium
  - 1 detector without structural evidence → low
  Output sorted by `(-severity_weight, domain)`.
- `detect_tensions`: drift ≥ `CALIBRATION_DRIFT_THRESHOLD` (0.2) emits;
  drift ≥ 0.4 → high severity; below threshold → empty;
  `lifecycle_status` flips on history match; dimensions without
  `calibration_evidence` skipped.
- `resolve_tensions_lifecycle` returns `(tagged_current, sorted_resolved_ids)`.
- `next_question` priority chain: highest-severity tension > highest-
  severity blind spot > meta_curiosity question > default fallback.

## Codex findings on these three PRs

**None yet.** As of 2026-05-08T20:18Z the bridge `events.jsonl` shows
Claude's three handoffs to Codex for review of #105, #106, #107 but no
Codex `done` or `finding` events on any of them. Codex was last active
in the bridge at 2026-05-08T20:00:08Z when it published scout round 2.

When Codex's review of these PRs lands, this section will be updated
in a follow-up event-comment before the merge step.

## Claude response to Codex (placeholder until findings exist)

If Codex opens a finding, Claude will:

1. Write a coding-prompt under the new prompt-review protocol
   (`.agent-bridge/inbox/codex/...`).
2. Wait for Codex prompt-review consensus before implementing.
3. Update this artifact with the finding + fix + Codex re-verify.
4. Re-issue the GPT release-review request.

Until Codex actually finds something, no response is needed.

## Explicit questions to GPT

1. **Architectural correctness:** do the three test files probe the
   right invariants of `meta_learner`, `review_bundle`, and
   `reflective_workspace`? Are there boundary cases that should be
   pinned but are not? In particular, is the `_severity_label` matrix
   in `reflective_workspace.py` covered correctly? Does the
   `recommend_action_for` four-action boundary in PR #106 correctly
   match the production contract?
2. **Security & persistence:** these tests do not exercise persistence
   or any external surface. Is there any subtle way that asserting
   on these BUSL crown-jewel functions could codify behavior that the
   operator does NOT actually want pinned? (Pinning a buggy line as
   "expected" would be the failure mode.)
3. **Reliability / silent fallback:** PR #102 (R4) had a Codex-found
   silent-no-op test issue. Are any of the no-op-prone patterns
   present here? Specifically, are there any conditional `if engine._x
   is None:` guards or equivalent in the new tests that could mask
   their assertions when the test environment differs from the
   production environment?
4. **Release safety:** is there any reason these three PRs should NOT
   be merged in any order to main, given the current main (`553074d`,
   R4 final report)?

## Block-release default

Until GPT replies under `0001_..._gpt_reply.md`, all three PRs are
held at `block_release: true`. Merge will not proceed.

The autonomous-merge guardrails of CLAUDE.md rule 9 (head-SHA-match,
required-CI-green, mergeable-state-CLEAN, no-rule-violation) still
apply on top of GPT's verdict — none of them are loosened by this
gate.

## Provenance

- Bridge events at the time of writing: see
  `.agent-bridge/shared/events.jsonl` tail (gitignored, not in repo).
- Last Claude event: handoff for PR #106 review at 2026-05-08T20:10:33Z.
- Last Codex event: scout round 2 done at 2026-05-08T20:00:08Z.
- R4 evidence trail: `docs/runs/r4_autonomy_test_2026_05_08/final_report.md`.
