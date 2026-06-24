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
| `message` | no | no | coordination only |
| `handoff` | no | no | coordination only |
| `wake_request` | no | no | scheduling only |
| `heartbeat` / `liveness` / `status` / `intent` / `claim` / `release` / `test` / `done` | no | no | informational |

A non-authoritative type **cannot change the gate verdict no matter what its
status name or body says** (fixes #3/#4/#5).

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
(fixes #6). *(Content-identical rebase carry-forward remains the CLAUDE.md 9a
exception, evaluated by patch-id, not by this classifier.)*

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
in §3.4 + head-expiry in §3.3) is run through **every** consumer's public entry
point, asserting **identical** verdicts. CI-blocking. Adding a new gate consumer
without wiring it into this test is a conformance failure. This is what makes the
single-taxonomy guarantee durable rather than aspirational.

## 6. Non-loosening / safety invariants

- Unknown `decision_status` → fail-closed (BLOCK / operator review). Never
  default-allow on uncertainty.
- RCO veto stays absolute + per-identity; author≠reviewer stays; head-exact
  binding stays; silence still BLOCKS.
- This spec **only changes HOW authority is read** (structured fields vs free
  text); it does not grant new authority, loosen any gate, or alter who may sign.
- Migrating a consumer to the shared module is **gate-classifier logic →
  off-allowlist → operator-sign**, landed per-consumer behind the §5 conformance
  test (no behavior drift permitted: the migration PR must show byte-identical
  verdicts on the existing corpus + the new §1 cases).

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
