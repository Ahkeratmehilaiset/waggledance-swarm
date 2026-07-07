# Bridge-Consensus Approval v1

**Status:** operator-authorized 2026-05-29 (plan-approval bootstrap).
**Authorization source:** operator directive 2026-05-29 — *"rakenna kuvan
mukainen järjestelmä ei pelkkää substraattia, kaikki ilman operaattori
kyselyitä, kyselyt hoidetaan bridgen consensuksella"* (build the storyboard
system, not just substrate; all without per-action operator queries; approvals
handled by bridge consensus). Extends the prior idle-window authorization in
`IDLE_AUTONOMY_CHARTER.md` (2026-05-18: *"konsensus on maailman tehokkaimmilla
agenteilla ei minulla"*).
**Companion docs:** `IDLE_AUTONOMY_CHARTER.md`, `STAGE2_CUTOVER_RFC.md`,
`CONTINUOUS_PLAN_TO_VISION.md`, `CLAUDE.md` (Rules 6/9/10).
**Enforcement point:** `tools/idle_consensus_auto_merge.py` (the
`bridge_consensus` approver is added in Track **T0b**; this doc — Track **T0a**
— defines the contract the code must implement).

## Purpose

Move the **approval authority for an autonomous MERGE** from a *per-action
operator query/signature* to a **verified three-agent bridge consensus**. This
lets the swarm land active-development PRs (the `CONTINUOUS_PLAN_TO_VISION.md`
tracks) at machine speed while preserving an independent safety gate.

This document governs **MERGE only**. It explicitly does **not** govern the
Stage-2 atomic-flip cutover (see §"Out of scope" and `STAGE2_CUTOVER_RFC.md`).

## The bridge-consensus contract (fail-closed)

An autonomous merge is approved **only if all of the following hold**. Any
missing, duplicated, forged, or stale signal fails closed to
`operator_review_required` — the gate never default-allows.

1. **Build consensus** — two verified build slots, the lead (`codex-lead-1`)
   and the tools/impl peer (`codex-tools-1`), concur on the change. For a PR
   authored by neither build identity, both build identities must post
   head-bound build-consensus approvals. For a PR authored by one of the build
   identities, the author's own build slot is explicitly **waived**, not
   approved: the author's own event is ignored for reviewer authority, the
   other build identity remains mandatory, and the verifier records
   `build_author_slot_waived` in the receipt material. There is no waiver for
   RCO authors or non-build authors, and a later block from the build author
   still blocks that waived slot. **Identity matching is head-bound, not
   label-bound (2026-06-05):**
   a `build_consensus_pass` event counts for a PR when its canonical PR scope
   matches and the event still binds the PR's exact head SHA, or when a
   descriptive `task_id` carries a structured head field (`payload.head`,
   `payload.head_sha`, etc.) that equals the PR's exact head SHA. `payload.pr`
   is PR scope only: it never proves head freshness, never substitutes for the
   exact head binding required by clause 6, and cannot make a stale or
   missing-head approval count. `head_sha` is a precise, unforgeable binding
   (the exact commit), strictly stronger than a free-form `task_id` string, so
   this *tightens* rather than loosens the gate while removing a class of silent
   stalls where valid concurrence under a descriptive `task_id` (e.g.
   `prNNN-refresh-current-main`) was invisible. Canonical-`task_id` match remains
   accepted; exact structured head match is the additional, authoritative key
   for descriptive labels. The non-author build peer is still subject to
   head-exact binding (clause 6).
2. **Independent RCO pass** — a **recognized RCO identity** posts an explicit
   `RCO_PASS` (`type=decision` with a status in the approval set) on the PR's
   **canonical task_id** (= branch name) at the **exact head SHA**. The
   recognized RCO set is `{claude-rco-1, claude-rco-2}` (backup-RCO co-authority,
   added 2026-06-05). A valid `RCO_PASS` from **either** identity satisfies the
   RCO slot, giving availability when one RCO is offline. The passing RCO **must
   not be the PR author** (author ≠ reviewer): if a recognized RCO authored the
   PR, only the *other* recognized RCO can satisfy the RCO slot.
3. **RCO veto is absolute and per-identity** — any `finding`/`changes_requested`
   from **any** recognized RCO identity on that task blocks the merge
   (`tools/check_bridge_changes_requested.py`). A veto **outranks a pass**: if
   one recognized RCO passes while the other holds an unretracted veto at the
   same head, the gate is blocked. RCO is never out-voted, and the backup RCO
   can never be used to out-vote a veto.
   **Descriptive veto symmetry (2026-06-05):** a later `changes_requested`,
   `blocked`, or equivalent consensus-block event from a build identity or
   recognized RCO under a descriptive `task_id` invalidates that identity's
   approval when the block carries the same exact structured `payload.head` (or
   equivalent head field) as the PR head. A stale head does not attach to the
   current head, and a missing or unverifiable descriptive head fails closed
   instead of silently cancelling or approving unrelated work. `payload.pr`
   remains PR scope only and never relaxes exact head binding.
4. **RCO absence = NO merge** — if no recognized RCO `RCO_PASS` at the exact head
   is present, the gate refuses even when build-consensus and every charter
   condition pass. Silence blocks; it does not default-allow.
5. **Independent slots** — the normal approval set is build-lead + build-tools +
   exactly one recognized RCO = three distinct verified identities. For a
   lead-authored or tools-authored PR, the author build slot is a recorded waiver
   and the approval set is the other build identity + exactly one recognized,
   non-author RCO. An RCO identity counts for the RCO slot only, never a build
   slot. Duplicate, missing, unverifiable, self-approving, RCO author-as-reviewer,
   or missing-peer-under-build-author-waiver signal sets fail closed. **"Verified"
   includes agent-uuid binding (2026-06-11):** the claimed
   `agent` label must carry the registered `agent_uuid` — see "Enforcement of
   agent-uuid identity binding" below.
6. **Head-exact binding** — all required, non-waived approvals bind to the exact
   head SHA. Any re-push that **changes content** invalidates all prior approvals;
   re-consensus is required (mirrors `gh pr merge --match-head-commit` and the
   PR #777 head-drift fail-close). **Exception — content-identical base rebase
   (2026-06-05):** a pure rebase onto current `origin/main` with **no content
   change** carries the consensus approvals (RCO_PASS + build_consensus) forward
   to the new head, because the reviewed content is unchanged. The verifier
   proves content-identity mechanically: the diff `merge_base..head` at the new
   head is byte-identical to the diff at the prior approved head (no
   conflict-resolution edits). CI **must be re-run green against the new head**
   before merge (carry-forward covers content review only, never CI — the
   advanced base can introduce semantic skew). Any content difference forfeits
   carry-forward and forces full re-consensus. This breaks the stale-base
   deadlock (every merge re-stales the rest) without weakening content review.
7. **All existing charter conditions still hold** — the seven parallel
   conditions in `IDLE_AUTONOMY_CHARTER.md` (CI green, receipt verified, rate
   limit, mergeable clean, allowlist match, no denylist hit) are unchanged and
   additive to this contract.
8. **MAGMA receipt** — the merge emits a MAGMA receipt recording the three
   identities, the head SHA, and the `RCO_PASS` event reference. A consumer
   must be able to **re-derive** the verdict from those fields; trusting a bare
   `ok` flag is forbidden (fail-open-recurs).

## Enforcement of backup-RCO co-authority (2026-06-05 amendment)

The recognized-RCO-set semantics above require these enforcement changes
(implemented by the impl lane, reviewed independently by the *other* RCO):

* `tools/check_rco_pass_present.py` — accept `--rco-agent` as a **set** (repeat
  the flag). Return ok when a valid `RCO_PASS` at the exact head exists from
  **any** recognized identity that is **not** the PR author, and is not
  superseded by that identity's own later veto. Default set `{claude-rco-1,
  claude-rco-2}`. Fail-closed on empty set / missing / malformed.
* `tools/idle_consensus_auto_merge.py` (`verify_bridge_consensus`) — treat the
  RCO slot as satisfied by exactly one recognized RCO identity (≠ author),
  count it for the RCO slot only, and keep the three-distinct-identity check.
  A veto from **either** recognized RCO at the head blocks (veto outranks pass).
* `tools/check_bridge_changes_requested.py` — already honors *any* peer veto
  (peer = agent ≠ merging-agent), so either RCO's `changes_requested` already
  blocks; no change needed, but its behavior is now contractually required.
* Executor (`Invoke-BridgeMergeDriver.ps1`, operator-side) — pass the recognized
  RCO set; record which RCO satisfied the slot in the receipt.

Required fail-closed tests: rco-1-only pass → ok; rco-2-only pass → ok;
author-RCO self-pass (rco-2 authored + rco-2 pass, no rco-1 pass) → not ok;
one passes + other vetoes at head → blocked; neither passes → blocked; pass at
stale head → blocked; duplicate identity → not three-distinct.

`tools/idle_consensus_auto_merge.py` and `CLAUDE.md` are on the charter file
denylist, so this amendment is **operator-gated** and lands with the operator's
signature (per "Bootstrap authority" below); it cannot ride its own gate.

## Enforcement of head-bound build-consensus matching (2026-06-05 amendment)

The head-bound matching (clause 1) requires (impl-lane implements, the other RCO
reviews):

* `tools/idle_consensus_auto_merge.py` (`verify_bridge_consensus`) — when
  collecting build identities for a PR, count a `build_consensus_pass` event if
  the existing `event.task_id == canonical_task_id`/PR scope matches and the
  event still binds the exact head, or if a descriptive event carries
  `event.payload.head == head_sha` (exact; including equivalent structured head
  keys such as `payload.head_sha`). The structured head match is the
  authoritative key for descriptive labels; `payload.pr` is never a head binding
  and must not make a stale or missing-head event count. Still require two
  **distinct** build identities.
* `tools/check_promotion_eligible.py` — thread the same head-bound match so the
  promotion verifier and the merge gate agree.
* Tests (fail-closed): build_consensus under a descriptive task_id but
  `payload.head == head` counts; build_consensus whose `payload.head != head`
  does NOT count (stale head rejected); two build events from the same identity
  still count as one (distinctness preserved); a forged event with no resolvable
  head does NOT count; `payload.pr` alone never satisfies head-exact binding.
  A later descriptive `changes_requested`/`blocked` event with
  `payload.head == head` invalidates that identity's approval at the same head;
  a descriptive block with a stale `payload.head` does not attach to the current
  head.

This removes the silent-stall class where valid concurrence under a descriptive
`task_id` (e.g. `prNNN-refresh-current-main`) was invisible to the gate, while
keeping head-exact binding and independent-slot requirements intact.

## Enforcement of build-author slot waiver (2026-06-27 amendment)

The lead/tools author waiver is an availability repair for the case where the
author is itself one of the two build-slot identities. It does not grant that
author reviewer authority over its own PR.

* `tools/idle_consensus_auto_merge.py` (`verify_bridge_consensus`) — when
  `author_agent == codex-lead-1`, mark the `build_lead` slot as
  `build_author_slot_waived=true`, ignore any lead self build event for direct
  approval, and still require a head-bound `codex-tools-1` build-consensus event.
  Symmetrically, when `author_agent == codex-tools-1`, waive only `build_tools`
  and still require a head-bound `codex-lead-1` build-consensus event.
  A later block from the build author makes the waiver unsatisfied until cleared.
* No RCO waiver exists: an RCO-authored PR still requires a different recognized
  RCO to satisfy the RCO slot.
* No arbitrary producer waiver exists: if the author is not `codex-lead-1` or
  `codex-tools-1`, both build slots remain mandatory.
* Receipts and reports carry the waiver state explicitly via
  `build_author_slot_waivers` and per-role `build_author_slot_waived`, so an
  auditor can distinguish a waived author slot from a direct author approval.

Required fail-closed tests: lead-authored + tools build + non-author RCO allows;
tools-authored + lead build + non-author RCO allows; lead-authored without tools
build refuses; tools-authored without lead build refuses; RCO-authored self-pass
refuses; duplicate build identity still refuses.

## Enforcement of agent-uuid identity binding (2026-06-11 amendment)

A verified identity is bound to its **session uuid**, not just its claimed
`agent` label. The operator-owned registry
`configs/bridge_identity_registry.json` (loaded via
`waggledance/core/bridge_identity_registry.py`) maps each gate identity to its
canonical `agent_uuid`; registry re-keying is operator-owned security
configuration and lands only via an operator-reviewed PR.

* Gate consumers (`tools/check_rco_pass_present.py`,
  `tools/check_bridge_changes_requested.py`, and `verify_bridge_consensus` in
  `tools/idle_consensus_auto_merge.py`) reject any gate-relevant event whose
  stamped `agent_uuid` is **missing**
  (`missing_uuid`) or does not match the registered binding for the claimed
  agent (`mismatch_uuid`), fail-closed. Rejected events are surfaced in the
  report (`ignored_identity_mismatch_events`) so audits can see attempted or
  accidental mis-signing instead of it disappearing silently.
* The lead-stall failover applies the same binding to its idle evidence: lead
  idle proof derives only from uuid-verified durable events; missing or
  mismatched lead uuid evidence refuses the failover (see the lead-stall
  amendment and its tests).
* Motivation: on 2026-06-11 a producer session mis-signed an `rco_pass` as
  `claude-rco-2`, and label-keyed matching counted it as the satisfying RCO
  reference (finding `wd/security/bridge-identity-binding-gap-20260611`). The
  registry binding makes that event class mechanically rejected.
* Fail-closed tests (live in `tests/tools/`): correct label + wrong uuid is
  not counted; missing uuid is not counted; uuid-verified events from the
  genuine identity still count; the failover refuses on missing lead uuid
  idle evidence.

Clause 5's "unverifiable ... signal sets fail closed" includes uuid-unbound
signals under this amendment: an event that cannot be uuid-verified never
fills a consensus slot.

## Out of scope (stays operator-gated)

The **Stage-2 atomic-flip cutover** (runtime read-path migration; e.g.
`hex_mesh.enabled` flip, `hybrid_retrieval candidate→production`, live
subdivision) remains **operator-signed** under `CLAUDE.md` Rule 10 and
escalation category 5 of the charter. Consensus-gated cutover is deferred to a
**separate future amendment (Track T0c)** explicitly gated on:

* a matured synthetic adversarial corpus (Track T5) beyond today's seed set;
* a proven, tested **auto-rollback** path for the flip;
* an existing **post-cutover verification harness**.

Granting cutover authority before those dependencies are real would make the
extra cutover gate hollow. This is a deliberate sequencing choice.

## Self-modification protection (bootstrap safety)

The gate must not be able to weaken itself via the mechanism it grants. Track
T0a therefore adds to the charter file denylist:

* `tools/idle_consensus_auto_merge.py` (the merge gate itself);
* `tools/check_bridge_changes_requested.py` (the RCO-veto preflight).

Together with the pre-existing denylist entries (`CLAUDE.md`,
`IDLE_AUTONOMY_CHARTER.md`, `STAGE2_CUTOVER_RFC.md`, the operator-gate
constants), this ensures any change to the consensus machinery itself remains
operator-merged — an autonomous bridge consensus cannot self-approve a change
to its own gate.

## Bootstrap authority for T0a/T0b

`CLAUDE.md` and the charter docs are on the denylist, so the PRs that
*establish* this mechanism cannot ride it. They land via normal PR + an
independent `claude-rco-1` `RCO_PASS` + build consensus, with the **operator's
2026-05-29 plan approval** as the one-time authorization for the governance
change itself. Subsequent (non-governance) PRs use the consensus contract
above.

## Operator emergency-stop remains authoritative

Nothing here weakens the operator's ability to stop the loop. The charter's
revocation layers still apply and must succeed even while the merge tool is
running: revert the charter file; add a `CHARTER_DISABLED` constant (on the
code-pattern denylist, so it operator-merges); or a direct bridge instruction
to all active agents. The operator owns the charter; the substrate proves every
merge against it.

## Standing consensus-sign for off-allowlist / high-scrutiny PRs (2026-06-25 amendment — DORMANT until bootstrap-signed)

**Status: DORMANT. This section has NO effect until the operator places an
explicit per-PR signature on BOTH bootstrap PRs (see Bootstrap below). Until then
the pre-existing rule stands: off-allowlist / high-scrutiny PRs require an
explicit per-PR operator signature.**

### Operator directive being captured

> "allekirjoitan parhaan mahdollisen konsensus-hyväksynnän JÄLKEEN kaikki,
> nyt + tulevaisuudessa, jatkakaa" — operator, 2026-06-25
> ("I sign — AFTER best-possible consensus-approval — all [PRs], now and in
> future; proceed", with the operator's own carve-out for Rule-10 / irreversible).

This removes the per-PR-sign bottleneck for the off-allowlist class by declaring
that, for that class, **the operator's signature is STANDING and is satisfied by
a defined "best-possible consensus" state** — captured here AUTHORITATIVELY (an
operator-signed amendment), never run from a chat relay. (Relay-only capture is
exactly what produced the #1387 auto-merge and the B-vs-C relay-ambiguity; a
relay does not change the gate — only a signed amendment does.)

### Definition — "best-possible consensus" (the operator-sign equivalent; FULL gate, fail-closed)

For an off-allowlist / high-scrutiny PR, the standing operator signature is
satisfied **iff ALL of the following hold at the exact head SHA**, evaluated
fail-closed (any missing, ambiguous, or unverifiable element →
`operator_review_required`, i.e. an explicit per-PR signature is still required):

1. **build_consensus@head** from **both** build slots — lead (`codex-lead-1`)
   AND tools (`codex-tools-1`), author-excluded (author ≠ reviewer).
2. **DUAL-RCO `RCO_PASS`@head** — **both** recognized RCOs
   (`claude-rco-1` AND `claude-rco-2`), mandatory. (This is **stronger** than the
   Rule-9a single-RCO-sufficient bar that governs allowlist-clean autonomous
   merge — the off-allowlist class earns the higher bar precisely because it is
   the class that previously required the operator's own eyes.)
3. **CI all required checks green@head** (the full required matrix, e.g. 6/6).
4. **NO unretracted veto/finding** from **any** recognized RCO on the PR's
   canonical task — RCO veto is absolute and per-identity and **outranks** a
   pass (`tools/check_bridge_changes_requested.py`).
5. **Charter checks pass** — `evaluate_paths` + `evaluate_diff_content` evaluated
   and recorded.
6. **Correct consensus computation** — head-exact binding (any content-changing
   re-push invalidates all prior approvals; content-identical base rebase carries
   forward per the P3 rule), three+ distinct verified identities, agent-uuid
   identity binding.
7. **MAGMA receipt** records the standing-sign basis (the directive reference, the
   approving identities, the head SHA, and each gate element) so a consumer can
   re-derive the verdict — never a bare "operator standing-signed" flag.

**Safety case (why this is adoptable):** at the #1387 bypass-merge the state was
rco-2 pass but **no** rco-1 pass **and** an active rco-1 finding. The definition
above (dual-RCO mandatory + no-unretracted-finding) is **strictly stronger** than
that state — so the standing rule, defined this way, **would have PREVENTED the
#1387 incident**. The standing rule does not loosen the gate; it makes the
operator-sign equivalent the *fullest* form of the gate.

### Scope of the standing sign — the (a)/(b) split (operator scope decision 2026-06-25)

Per the operator's direct scope decision (2026-06-25, via AskUserQuestion in the
fable-5 session, relayed on this task; **ratified by the operator's signature on
this amendment**), the off-allowlist / high-scrutiny class is split by the test
**"does the merge gate READ/EXECUTE it at runtime to compute a merge/sign/veto
verdict?"**:

**(a) ALWAYS operator-EXPLICIT (never rides standing consensus) — verdict-computing
+ core governance.** A real per-PR operator signature is required regardless of any
consensus state for:

* the **Stage-2 atomic-flip cutover** and everything under Rule 10 / escalation
  category 5 (already out of scope above; Track T0c gates it separately);
* any **irreversible or outward-facing** action (publishes, deletes, force-pushes,
  external sends, secret/credential changes);
* the **core governance docs the gate itself reads/obeys**: `CLAUDE.md`,
  `IDLE_AUTONOMY_CHARTER.md` + its loader `waggledance/core/idle_consensus_charter.py`,
  and **this contract** (`BRIDGE_CONSENSUS_APPROVAL_V1.md`);
* the **verdict-computing gate code** — anything the gate reads/executes at runtime
  to compute a merge / sign / veto verdict: `tools/verify_bridge_consensus.py`,
  `tools/check_bridge_changes_requested.py`, `tools/check_rco_pass_present.py`,
  `tools/idle_consensus_auto_merge.py`, `tools/merge_with_bridge_receipt.py`,
  `tools/write_bridge_consensus_merge_receipt.py`, and
  `tools/check_proven_safe_autosign_class.py` (the P1 auto-sign checker — operator
  named it explicitly: "the P1 auto-sign checker once it would compute");
* **the wiring step itself** — any PR that **wires a dormant tool INTO the gate's
  runtime verdict path** (e.g. the cause-B fix wiring `bridge_event_taxonomy` into
  `check_bridge_changes_requested`) is (a), because it touches a verdict-computing
  consumer and changes what the gate computes;
* the **two bootstrap PRs** (#1393 + this amendment; see Bootstrap).

This is the existing **Self-modification protection** principle restated: *the gate
must not be able to weaken itself via the mechanism it grants.* A change on the
gate's runtime verdict path is off-allowlist **and** operator-explicit.

**(b) RIDES the standing sign (dual-RCO best-possible consensus) — gate-ADJACENT,
NOT on the runtime verdict path.** These are denylisted (high-scrutiny → full
treatment) but the operator's standing signature covers them once best-possible
consensus holds:

* the **gate-policy / design SPEC DOCS** (`P1_PROVEN_SAFE_AUTOSIGN_CLASS*`,
  `BRIDGE_EVENT_GATE_TAXONOMY*` *spec doc*, `P3_CONTENT_IDENTICAL_REBASE*`, the
  `P4_*` specs) — they **describe**; the gate does not read them at runtime;
* **dormant tools NOT yet wired** into the gate: `tools/bridge_event_taxonomy.py`
  (until the cause-B wiring PR — which is (a)), `tools/auto_rollback_eligibility.py`
  (until wired to trigger reverts — that wiring is (a)),
  `tools/post_merge_canary.py` (observability-only — no merge/sign authority even
  when wired);
* the **P4c corpus + its validator** (`tests/security/p4c_corpus/**`) — CI
  integrity tests, not read by the merge gate at runtime. *(Borderline:
  `validate_p4c_corpus.py` is the anti-tamper anchor for the auto-sign soundness
  proof; placed in (b) per the runtime-read test but flagged for rco-1's corpus-
  domain confirmation — tightening it to (a) is acceptable if rco-1 prefers.)*

**Mechanical rule (so the line never needs re-bikeshedding):** a file is **(a)** iff
it is `CLAUDE.md` / the charter / this contract, **or** the merge gate
reads/executes it at runtime to compute a verdict (incl. a PR that *puts* it on that
path). Everything else off-allowlist is **(b)**. A dormant tool **migrates from (b)
to (a)** the moment a PR wires it into the gate — and that wiring PR is itself (a).
Borderline membership is decided by lead + rco-1 jointly (operator-delegated); when
in doubt, **(a)** (fail toward operator-explicit).

### Bootstrap (no circular self-authorization)

You cannot use "consensus = sign" to authorize the rule that creates it. The rule
activates only after the operator places an explicit per-PR signature on **both**:

* **(a)** PR #1393 — the charter gate-policy/gate-ops denylist (the structural
  recognition that this class is high-scrutiny); and
* **(b)** the PR carrying THIS amendment (CLAUDE.md Rule 9b + this section).

Both are off-allowlist (`CLAUDE.md` and this contract are denylisted →
`allowed=False`), so neither can ride the mechanism it establishes. After both are
operator-signed and merged, the standing rule applies to all subsequent
off-allowlist / high-scrutiny PRs **except** the carve-outs above — subject to the
**Activation precondition** below.

### Activation precondition — cause-B latch must be fixed first (rco-2 fence, PR #1396)

**Why this is mandatory.** Making consensus *the operator's signature* AMPLIFIES
every defect in the consensus computation into an **operator-signature bypass**,
not merely an RCO bypass. Element 4 of the definition ("no unretracted
veto/finding from any recognized RCO") is computed by
`tools/check_bridge_changes_requested.py`, which today still has the **cause-B
free-text latch fail-open** (the #1387 defect class): a recognized-RCO veto posted
with a non-latching token (a free-text / `hold` / `veto` status rather than
`changes_requested`/`blocked`), or a pass-then-mistokened-veto at head, does **not**
latch as a block → element 4 clears → the PR would receive the STANDING operator
signature **despite a live RCO veto**. Under consensus-as-sign that bypasses the
operator's signature. The standing rule must therefore NOT be relied upon while
that fail-open is open.

**Precondition (fail-closed — ALL must hold before the standing rule is treated as
ACTIVE, in addition to the two bootstrap signatures):**

1. **Bootstrap signatures** — explicit operator-sign on PR #1393 **and** this
   amendment PR (above).
2. **Cause-B fixed AND WIRED into the live consumer** — the
   "recognized-RCO finding blocks by **TYPE**" fix (the P2/D5 gate-event taxonomy,
   `BRIDGE_EVENT_GATE_TAXONOMY_V1.md` / `tools/bridge_event_taxonomy.py`, #1387/#1388)
   is wired into `tools/check_bridge_changes_requested.py` so element 4 latches a
   recognized-RCO veto by event **type/authority**, not by free-text token
   presence. A mistokened or free-text RCO veto at head must route element 4 to
   **blocked**.
3. **Verification harness CI-green** — a conformance corpus / P4c-style harness
   proves, in CI against the live consumer, that element 4 **cannot be cleared**
   by a mistokened / negated / free-text recognized-RCO veto at head (the negated
   and mistokened forms explicitly enforced, not just the happy path).

**Interim posture until the precondition is met (residual documented per the fence
option-b belt):** the existing **per-PR operator signature stands** for the
off-allowlist class; the standing rule is **dormant** even after the two bootstrap
signatures until elements 2–3 land. While dormant, the gate relies on:
dual-RCO-BOTH-pass + the gate-governance carve-out + the operational requirement
that **recognized RCOs MUST use latching `changes_requested`/`blocked` block
vocabulary** for any veto (a free-text "hold" does not latch — see the
status-field-block-vocabulary discipline). The cause-B latch-fix + harness is a
**named fast-follow** (the P2/D5 taxonomy-wiring track), not an open-ended TODO.

Net: element 4 is only *sound* once its computation cannot be cleared by a
mistokened veto. The standing rule activates when that soundness is proven in CI —
never before.

### Complementarity with the charter denylist (#1393)

The denylist and the standing rule are **complementary, not redundant**: the
denylist makes the gate-policy/gate-ops class **off-allowlist** (so it receives
the FULL best-possible-consensus treatment instead of silently auto-merging
path-clean — the #1387 hole), and the standing rule says that class's
operator-sign **is** best-possible-consensus — except gate-governance changes,
which stay operator-explicit per the carve-out above. Both are required.

### Operator emergency-stop and revocation remain authoritative

This amendment does not weaken the operator's stop/revocation layers. Reverting
this section (an edit to this denylisted contract) is itself off-allowlist and
operator-merged; the standing rule can be withdrawn at any time the same way it
was granted.

### Gate-code ratification record — PR #1477 (2026-07-07)

PR #1477 (`fix(gate): unverified recognized-RCO vetoes latch fail-closed`) was
merged on 2026-07-02 at merge commit
`543f0aefefa1c88d4dd161d418ff7a204a1c7655` from head
`3e10b3c18fa21cb9e65dec9a3e9a76a052370628`. The change makes unverified
block-shaped events from a recognized RCO name latch fail-closed as a block,
while still giving unverified passes no approval credit. This is the safe
asymmetry for identity-registry drift: honoring a forged veto can cause a
spurious hold, while dropping a real veto can merge past an active block.

The merge touched live gate code and therefore belonged to the operator-explicit
gate-governance class. The operator ratified the already-merged fail-closed
outcome on 2026-07-07 for #1477 only, closing the recorded confirm-or-revert
item from the 100H last-mile sprint board. This ratification does **not** create
a standing exception for future gate-code changes: any later edit to the merge
gate, RCO-veto path, exact-head approval logic, or this contract remains
operator-explicit under the carve-out above.

## Versioning

* v1 (this doc): three-agent fail-closed MERGE consensus; cutover explicitly
  out of scope. Enforcement lands in T0b; cutover loosening deferred to T0c.
* v1.1 (2026-06-25 amendment, DORMANT until bootstrap-signed): standing
  consensus-sign for the off-allowlist / high-scrutiny class — best-possible
  consensus (dual-RCO + full fail-closed gate) substitutes for the per-PR
  operator signature, except Rule-10/cutover, irreversible/outward-facing
  actions, and the gate-governance class. Bootstrap = explicit operator-sign on
  PR #1393 + the amendment PR.
* v1.2 (2026-07-07 record): operator ratification recorded for the already
  merged #1477 fail-closed unverified-recognized-RCO-veto gate-code fix; future
  gate-code changes remain operator-explicit.
