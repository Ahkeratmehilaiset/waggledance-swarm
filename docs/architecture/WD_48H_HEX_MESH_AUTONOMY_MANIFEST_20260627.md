# WD 48H Hex-Mesh Autonomy Manifest - 2026-06-27

Status: active 48 hour execution manifest.
Owner: codex-lead-1.
Window: 2026-06-27T16:17:27Z to 2026-06-29T16:17:27Z.

This file turns the operator-provided WaggleDance storyboard into a durable
execution target for the bridge agents. It is not a claim-safety upgrade. The
source-of-truth claim guard remains `WD_IMAGE1_FUNCTIONALITY_MANIFEST.md` and
`tools/wd_image1_capability_manifest.py`.

## North Star

Build toward a deterministic-first WaggleDance Swarm AI:

- every candidate growth step starts as an advisory, read-only, or shadow proof;
- authoritative solver decisions remain deterministic and auditable;
- LLM output stays advisory and cannot override deterministic solver authority;
- MAGMA evidence is append-only or explicitly marked as not yet hard-enforced;
- low-risk autogrowth proposes bounded, reversible work and never activates
  irreversible runtime mutation without the existing gates;
- hex subdivision, ring messaging, and parent-child hierarchy move from shadow
  proof toward runtime-ready evidence without claiming production activation.

## Big-Picture Progress

Current storyboard readiness at sprint start: about 32%.

Current storyboard readiness after the #1412 through #1416 exact-head queue
drain: about 39%.

Target readiness after this 48 hour sprint: 38% to 42%.

This is product/vision readiness, not a percentage of repository files complete.
No literal image claim becomes safe merely because this sprint lands. Claim
safety changes only when the proof tool and RCO review support them.

| Storyboard area | Start | Current | 48h target | Sprint focus |
| --- | ---: | ---: | ---: | --- |
| Smart router and hex entry | 45% | 46% | 47% | Keep router evidence current; no every-query claim flip. |
| Deterministic solver plus MAGMA audit | 50% | 52% | 53% | Preserve deterministic-first authority and audit evidence. |
| Low-risk autonomy loop | 30% | 36% | 38% | Add self-drive queue substrate and hard advisory boundaries. |
| Hex subdivision, ring, hierarchy | 15% | 25% | 25% | Merge offline proof and invariant evidence; do not claim runtime activation. |
| Self-organizing swarm mesh | 10% | 12% | 13% | Keep as roadmap, only measured/shadow evidence. |
| Future scale and industrial efficiency | 5% | 7% | 8% | Produce queueable benchmark and evidence tasks, not claims. |

## 48H Deliverables

1. Durable self-drive substrate
   - Tools lane builds a read-only queue planner/status report.
   - The planner may propose next actions, but must not append bridge events,
     claim work, enqueue scheduler items, call GitHub, decide consensus, or
     merge.
   - Live bridge smoke is mandatory because bridge text can describe bridge
     states and can trip unsafe free-text classifiers.

2. Roadmap and sprint board
   - Lead publishes this manifest and the sprint board in `docs/runs/`.
   - The board records current percent, 48h target percent, owners, blockers,
     and next sprint seed tasks.
   - After the 48h window, the next sprint is chosen from the highest-priority
     unblocked board item without waiting for a manual operator kick.

3. Hex subdivision and ring proof
   - Fable lane owns the next deterministic shadow/offline proof.
   - Proof must show parent-child subdivision and ring routing invariants.
   - It must keep runtime mutation authority false.

4. Governance and adversarial review
   - RCO1 owns guardrail review for dormant, bounded, reversible autogrowth.
   - RCO2 owns live-system smoke, status/payload-derived state checks, and the
     deterministic-solver versus LLM-advisory authority boundary.
   - Operator sign is still required for irreversible activation or denylisted
     surfaces.

## Standing Self-Drive Protocol

When a sprint finishes or stalls, agents continue without an operator prompt if
all of the following are true:

- the next action is read-only, docs-only, tests-only, or otherwise reversible;
- no credential, payment, legal/security exception, destructive operation, or
  unresolved write-scope conflict is needed;
- bridge status shows no higher-priority exact-head review, CI red item, or
  active claim conflict;
- the action has a named owner and a narrow write scope;
- the action preserves existing merge, RCO, build-consensus, denylist, and
  operator-gate rules.

Lead must stop and ask only when the normal repo rules require it. Otherwise,
lead posts the next sprint objective to bridge, agents claim their lanes, and
work continues.

## Guardrails Imported From RCO Input

- Dormant and fail-closed by default: the grower proposes, never self-activates.
- Bounded and reversible: every growth action has a scoped rollback story.
- MAGMA evidence must be re-derivable; do not trust bare flags.
- State transitions must be derived from structured status or payload fields,
  not arbitrary event prose.
- LLM fallback is advisory-only and structurally unable to override the
  deterministic solver.
- Live-system smoke is required for tools that read the bridge, MAGMA, runtime
  status, or control-plane state.
- Re-authoring for build-slot independence must use a neutral non-build,
  non-RCO identity when needed; RCO authors cannot review their own work.

## Current Concrete State

- PR #1410 and PR #1411 are merged and post-merge main CI was green.
- PR #1412 opened the first self-drive queue planner slice and was merged by
  exact-head squash into main at
  `04812c0674973508723c2f0de021c030372f564a`.
- Lead, RCO1, RCO2, local affected tests, live path-free smoke, charter path
  evaluation, and GitHub CI were green for #1412 content. The operator standing
  signature was used only for the `codex-tools-1` author-slot governance gap,
  not to bypass content review.
- Durable follow-up remains: codify a symmetric tools-slot waiver or
  wrapper-recognized neutral re-author path so future tools-authored PRs do not
  need manual governance handling after content gates are green.
- PR #1413 added this manifest and the first board snapshot, then merged at
  `c08f71d6ba851e58ddcb9c33ba535849e1549cc6`.
- PR #1414 added the first offline parent-child plus ring-routing invariant
  proof and merged at `1004b04f523d219b71862f3b7775c89d69fc15f3`.
- PR #1415 extended ring-delivery observability proof coverage and merged at
  `319b4dadb8d817b9d5aec17b25685b337a2bd8ca`.
- PR #1416 added subdivision-operation invariant proof coverage and merged at
  `5c3692d5a373f1ee05e23a78c6241b160b805f82`; main CI started on that merge
  commit.
- PR #1417 is the current truth-refresh PR for this manifest and the sprint
  board after the rapid #1414 through #1416 queue drain.

## 48H Exit Criteria

The sprint is successful if, by 2026-06-29T16:17:27Z:

- at least one self-drive queue artifact is merged or at an exact-head green
  review state;
- the sprint board lists the next three unblocked tasks with owners;
- at least one hex subdivision/ring/hierarchy proof or review item is ready;
- every new autonomy-loop artifact has a live-system smoke result where
  applicable;
- no new artifact grants merge, consensus, runtime mutation, or operator-gate
  bypass authority by implication.
