<!-- SPDX-License-Identifier: BUSL-1.1 -->
# Bridge Event Gate Taxonomy (V1) — RFC item **P2/D5**

**Status:** DRAFT spec (design-first, like the P1 spec). Producer-authored
(`fable-5`); reviewed by both recognized RCOs; gate-classifier *logic* is
off-allowlist → operator-sign before any consumer is migrated. **This document
changes no runtime behavior**; it defines the single shared taxonomy + invariants
that the gate consumers must converge on, and the conformance test that enforces
convergence.

RFC: *WD Bridge Throughput, Resilience & Pool-Decorrelation*, **P2 — "Generalize
the structured-event gate taxonomy (D5)"** *(rco-1 P2 / rco-2 P5)*.

## 0. One-paragraph summary

Every gate consumer currently re-derives "is this event a veto / an approval?"
from **free-text status NAMES and message bodies**, each with its own ad-hoc
substring logic. That has produced a recurring family of defects — fail-open on
negated tokens, fail-closed on resolution tokens, phantom self-blocks from
coordination messages, non-authoritative event types read as vetoes, and stale
blocks that never expire. This spec replaces the free-text reads with a **single
shared taxonomy** that classifies authority from **structured fields only**,
restricts veto/approve power to **authoritative event types**, binds blocks to a
**head SHA with auto-expiry**, and is **negation-aware** — enforced by a
**mandatory cross-consumer conformance test** so the consumers can never diverge
again.

## 1. The defect class this kills (empirical — every one is a real instance)

| # | Instance | Root cause | This spec's fix |
|---|----------|-----------|-----------------|
| 1 | #1368 negation fail-open | token-PRESENCE classifier flips on a good token (`resolved`/`cleared`) → negated forms (`block_not_resolved`, `not_yet_cleared`) slip through → merge past a live veto | §3 enum classification (not substring presence); §3.4 negation-aware |
| 2 | `changes_requested_resolved` false-block | `_is_blocking_status` over-blocks ANY `changes_requested_*` resolution status as a fresh veto | §3 explicit status enum; resolution statuses map to CLEAR, not BLOCK |
| 3 | status-field block-vocabulary self-phantom-block | a `type=message` whose STATUS NAME contained `blocked`/`changes`+`requested` was read as my own veto | §2 authority from `type`+`decision_status`, NEVER the status name string |
| 4 | #1384 `wake_request` phantom block | a `wake_request` whose status name held the `changes`+`requested` pair was read as a veto on the task | §2.1 only AUTHORITATIVE event types can veto; `wake_request`/`message`/`handoff` never can |
| 5 | driver merged past `message`-type concurrence "veto" | a `type=message` concurrence treated as authoritative by one consumer, ignored by another | §4 single shared classifier; §2.1 message can neither veto nor approve |
| 6 | stale `vetoed_after_pass` self-block | an RCO `finding` at an OLD head kept blocking a NEW head until manual retraction | §3.3 blocks are head-bound + auto-expire on content-changing re-push |

## 2. Authority comes from STRUCTURED FIELDS, never free text

A gate-relevant event is authoritative **only** by its structured fields. The
classifier MUST read exactly these and MUST NOT parse the status NAME string or
the message body for authority:

- `type` — the event type (enum, §2.1).
- `decision_status` — for `type=decision`, the canonical status enum (§3); for
  other types it carries no authority.
- `head_sha` — the 40-char head the event binds to (§3.3).
- `author_uuid` / `author_identity` — the verified signer (identity-bound, per the
  bridge identity-binding contract); free-text `agent` is advisory only.
- `task_id` — the canonical task the event applies to.
- `retracts_event_id` (optional) — explicit retraction pointer (§3.3).

> **Hard rule:** the status NAME and the message body are **human-readable
> annotations with ZERO gate authority**. A consumer that branches on a substring
> of either is non-conformant. (This retires the `check_status_name_safe.py`
> linter from a *guard* to a *lint convenience* — names can be sloppy without
> creating phantom blocks, because names are never read for authority.)

### 2.1 Only authoritative event types can veto or approve

| `type` | Can VETO? | Can APPROVE? | Notes |
|--------|-----------|--------------|-------|
| `decision` | yes (status∈BLOCK set) | yes (status∈APPROVE set) | the ONLY approving type |
| `finding` (recognized RCO) | yes | no | RCO veto is absolute + per-identity |
| `blocked` | yes (by type) | no | **PRESERVED** live merge-veto type (see reconciliation) |
| `message` | no | no | coordination only |
| `handoff` | no | no | coordination only |
| `wake_request` | no | no | scheduling only |
| `rco_review` / `test` / `done` | no | no | dropped live types — deliberate tightening (below) |
| `heartbeat` / `liveness` / `status` / `intent` / `claim` / `release` | no | no | informational |

A non-authoritative type **cannot change the gate verdict no matter what its
status name or body says** (fixes #3/#4/#5).

#### 2.1.1 Reconciliation with the LIVE gate (no silent loosening) — rco-1 finding + emit-audit

The live gate (`check_bridge_changes_requested.py @origin/main`) carries
`BLOCKING_EVENT_TYPES = {decision, rco_review, finding, blocked, test}` and CLEAR
types `{decision, rco_review, finding, done}`. This spec's set must not **silently
drop** a live BLOCK-authority type (that would be a real loosening — a currently
counted veto would stop counting — contradicting §6/§7). Each divergence below is
**evidence-based** (rco-1 emit-audit over 65,440 shared-log events, 2026-06-25) and
is a **safe-direction** change (only ever ADDs/keeps blocks, never drops a veto):

- **`blocked` → PRESERVED as veto-authoritative.** 70 events; **38 ACTIVE PR-task
  merge-vetoes since 2026-06-01** (`build_consensus_blocked_pending`,
  `merge_blocked_operator_or_driver`, `build_slot_blocked_tools_author`, …).
  Dropping it would be the real loosening; it stays (block-by-type).
- **`rco_review` → DROPPED.** **0 emit instances, ever** — RCOs veto via `finding`
  / `decision`, never `rco_review`. Dead authority; safe no-op, with the RCO veto
  fully preserved via `finding`.
- **`test` → DROPPED from authority** (deliberate tightening). 1433 events: 1252
  CI/smoke clears + 18 block-vocab. CI noise must not be a bridge gate signal;
  dropping it is the safe direction (a dropped clear leaves blocks standing). The
  18 block-instances are verified non-load-bearing before migration #3 ships.
- **`done` → DROPPED from CLEAR authority** (deliberate tightening). 199 clears; a
  dropped clear only leaves blocks standing (fail-closed-safe).

So §6's "non-loosening" holds as: **no BLOCK-authority is dropped** (`blocked`
preserved, `rco_review` proven dead), and the CLEAR-side drops (`test`/`done`) are
safe tightenings that can only keep blocks standing.

## 3. The canonical `decision_status` enum

`decision_status` is a **closed enum**; an unrecognized value fails **closed**
(treated as a BLOCK requiring operator review — never default-allow on unknown).

- **APPROVE set** — `build_consensus_pass`, `rco_pass`, `no_changes_requested`,
  `operator_sign`.
- **BLOCK set** — `changes_requested`, `finding` (RCO), `veto`, `hold`.
- **CLEAR set** (retracts a prior BLOCK by the same identity) —
  `changes_requested_resolved`, `changes_requested_cleared`, `block_retracted`.
- **NEUTRAL** — anything else authoritative-but-non-verdict.

### 3.1 Latest-authoritative-signal-per-identity wins
The verdict per `(task_id, author_identity)` is the **latest** authoritative
event (`decision`/RCO-`finding`) by `ts_utc`. A later CLEAR/APPROVE from that
identity supersedes its earlier BLOCK; a later BLOCK supersedes an earlier
APPROVE. Non-authoritative events never enter this computation.

### 3.2 Veto outranks pass across identities
If any recognized RCO identity holds an unretracted BLOCK at the current head, the
gate is blocked even if another identity APPROVES (veto is absolute, per Rule 9a).

### 3.3 Blocks are HEAD-BOUND and AUTO-EXPIRE
A BLOCK binds to its `head_sha`. When the PR head advances via a **content-changing
re-push**, every prior-head BLOCK and APPROVE **auto-expires** (consensus must be
re-established at the new head) — this is the existing head-exact-binding rule,
made mechanical. A BLOCK is cleared by: (a) a CLEAR/APPROVE from the same identity
at ≥ the block's head, (b) an explicit `retracts_event_id`, or (c) head
auto-expiry. There is **no** "carry a stale old-head block onto a new head"
(fixes #6). *(Content-identical rebase carry-forward remains a specified but
currently unimplemented CLAUDE.md 9a / P3 carve-out. If it lands, patch-id proof
belongs in gate code, not in this classifier.)*

### 3.4 Negation-aware classification
Classification is by **exact enum match**, not substring presence. A status that
is a negated/qualified form of an APPROVE/CLEAR token (e.g. `block_not_resolved`,
`not_yet_cleared`, `changes_requested_not_resolved`) is **not** in the CLEAR set —
it is an unrecognized value → fail-closed BLOCK (fixes #1). The conformance corpus
(§5) MUST include the negated form of every CLEAR/APPROVE token as a
fail-closed case.

## 4. Single shared classifier (no per-consumer re-implementation)

One module — `tools/bridge_event_taxonomy.py` — exposes the taxonomy + a pure
`classify(events, *, task_id, head_sha, recognized_rcos) -> GateVerdict`. Every
gate consumer imports it and **must not** re-derive authority locally:

- `check_bridge_changes_requested.py`
- `check_rco_pass_present.py`
- `verify_bridge_consensus.py`
- `Invoke-BridgeMergeDriver` (via the Python checkers it already shells out to)

Divergent local logic is the bug (instance #5). The module is the single source of
truth; consumers are thin adapters over `classify`.

## 5. Mandatory cross-consumer conformance test

A single fixture corpus of events (covering every row in §1 + the negated forms
in §3.4 + head-expiry in §3.3 + the §2.1.1 reconciliation cases) is run through
**every** consumer's public entry point, asserting **identical** verdicts.
CI-blocking. Adding a new gate consumer without wiring it into this test is a
conformance failure. This is what makes the single-taxonomy guarantee durable
rather than aspirational.

> **The corpus MUST be seeded from CURRENT REAL VERDICTS, not from this spec's
> sets** (rco-1). If the fixtures are derived from the new taxonomy, a loosening
> ships "conformant" — the byte-identical proof passes against a corpus that
> already baked in the loosening. So each migration (#3) computes the live gate's
> verdict on a sample of real shared-log events FIRST, then asserts the new
> classifier returns the identical verdict (except the §2.1.1 documented
> tightenings, each proven to only keep/add a block). **Shared fixture set with
> the P4c adversarial corpus** (rco-1/rco-2, `wd-p4c-...`): one fixture base, two
> assertion lenses — §5 = "all consumers agree"; P4c = "the P1 sign-waiver checker
> can't reopen an RCE class."

## 6. Non-loosening / safety invariants

- Unknown `decision_status` → fail-closed (BLOCK / operator review). Never
  default-allow on uncertainty.
- RCO veto stays absolute + per-identity; author≠reviewer stays; head-exact
  binding stays; silence still BLOCKS.
- This spec **only changes HOW authority is read** (structured fields vs free
  text); it does not grant new authority or alter who may sign. **No BLOCK
  authority is dropped** (`blocked` preserved; `rco_review` proven dead by
  emit-audit). The only verdict changes are the §2.1.1 **safe-direction
  tightenings** (`rco_review`/`test`/`done`), each proven to only keep/add a block,
  never drop a real veto.
- Migrating a consumer to the shared module is **gate-classifier logic →
  off-allowlist → operator-sign**, landed per-consumer behind the §5 conformance
  test. Behavior drift is permitted ONLY for the §2.1.1 documented tightenings:
  the migration PR must show verdicts **identical to the live gate on a
  real-verdict corpus EXCEPT** those enumerated tightenings, each accompanied by
  its emit-audit evidence and a proof it only keeps/adds a block.

## 7. Rollout (each via the consensus gate; no bundling)

1. **This spec** — design + invariants (producer-authored; dual-RCO review;
   operator-sign the invariant). Charter-allowlist-clean by path, operator-sign by
   content (gate-policy).
2. **`tools/bridge_event_taxonomy.py` + conformance corpus** — the shared module
   and §5 test, consulted by nothing yet (dormant, pure logic). Off-allowlist
   (gate-classifier logic) → operator-sign.
3. **Per-consumer migration PRs** — one consumer at a time, each proving identical
   verdicts via §5 before/after. Off-allowlist → operator-sign each.

## 8. Ownership

- **fable-5 (producer)** authors this spec (#1) and may author the dormant module
  + corpus (#2) — same producer-authors-gate-tooling pattern as P1 (a recognized
  RCO must not author the rules it enforces).
- **codex-tools-1** build-signs + runs the conformance corpus.
- **claude-rco-1 / claude-rco-2** adversarially fence the taxonomy (every §1
  instance + negated forms + head-expiry + unknown-status fail-closed) and review
  each migration; veto absolute.
- **codex-lead-1** coordinates; **operator** signs the invariant (#1) and each
  off-allowlist landing (#2, #3).
