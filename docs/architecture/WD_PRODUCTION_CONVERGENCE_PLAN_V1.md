# WD Production Convergence Plan V1

**Status:** canonical production execution order. This document defines
measurement and authority boundaries only. It grants no merge, cutover,
runtime authority, operator approval, or `claim_safe` flip.

**Decision:** P0 -> P1 is the only critical path until production-linked
served-traffic ratios exist. Solver breadth, Dream, game theory, CEGIS, hex
activation, and multi-instance work must not delay it.

## Normative hierarchy

1. Machine truth: `tools/wd_image1_capability_manifest.py` and
   `tools/build_wd_vision_progress_counters.py`.
2. Claim conditions: `IMAGE1_CLAIM_SAFE_RUNTIME_MILESTONES.md`.
3. Target rubric: `WD_VISION_MANIFEST_V1.md`.
4. Execution order: this document.
5. Governance: `CLAUDE.md` Rules 9 and 10, the idle charter, exact-head RCO
   review, and explicit operator gates.

`CONTINUOUS_PLAN_TO_VISION.md` remains historical authorization and backlog
context. This document supersedes its current-state claims and T1-T7 phase
order.

## Baseline

Original main snapshot date: 2026-07-15. Assessed `origin/main`:
`c682182c7fd42e4250b33cded290e899d0c3fccc`. Critical-path draft-PR rows
were refreshed on 2026-07-27; they are not claims that those drafts landed on
the original main snapshot.

| Signal | Snapshot truth |
|---|---|
| `proof_ok_count` | `6/6` |
| `claim_safe_count` | `0/6` |
| `production_safe_capability_count` | `0/6` |
| `all_literal_claims_safe` | `false` |
| Landed measurement substrate | #1518, #1519, #1524, #1525 |
| #1540 | `42627ce7`; pending-query identity refresh; draft; exact-head merge/operator gates remain |
| #1541 | `c55cf5ad`; chat-receipt verifier hardening; draft; exact-head merge/operator gates remain |
| #1543 | `a22391ee`; per-query receipt adapter integration containing the #1540/#1541 content; draft; exact-head merge/operator gates remain |
| #1568 | `5c316f4a`; fail-closed junction leaf stacked on #1543; draft; local handoff evidence exists, but exact-head CI/RCO/operator gates remain |
| #1571 | `458af5b4`; process-owned served-window lifecycle stacked on #1568; draft; local full suite is green, but the stacked base receives no PR CI and exact-head RCO/operator gates remain |
| #1572 | `cef2cef2`; receiver-owned two-state replay registry stacked on #1571; draft; exact-snapshot local full suite is green, but the stacked base receives no PR CI and exact-head RCO/operator gates remain |
| #1530 | `54f9fa6f`; includes #1532 bounds; CI 6/6; full exact-head review and explicit operator gate remain; optional advisory game-theory lane, not on P0/P1 |
| #1529 | `a827de6a`; incremental bridge reader; operator-gated infrastructure, not production capability |

Refresh this table after every critical-path merge. A green proof inventory is
not production progress unless a production counter or a named Rule-10
prerequisite moves.

The independent Grok 4.5 advisory that challenged the prior ordering is pinned
in `docs/architecture/GROK_4_5_PRODUCTION_CONVERGENCE_REVIEW_2026_07_15.md`.

## Execution laws

- Serving may fail open. Measurement and claims always fail closed.
- Count every served event exactly once, including gaps and receipt failures.
- Re-derive verdicts from receipts. Never trust producer `ok`, `bound`, or
  `claim_safe` flags.
- Raw queries, responses, paths, credentials, and payloads stay outside
  evidence artifacts.
- A slice counts as critical-path production-convergence progress only if it
  advances a production counter, a runtime default, or a named Rule-10
  prerequisite. Side lanes run only on surplus capacity.
- Solver count is not intelligence. Growth counts only when
  `marginal_verified_coverage_gain > 0`, or when correctness-preserving cost or
  latency improves on held-out cases.
- No consciousness claim is made. The measurable target is broader verified
  capability, calibrated self-knowledge, and bounded autonomous correction.

## Prerequisite DAG

```text
main: #1518 + #1519 + #1524 + #1525
                 |
          +------+------+
          |             |
       #1540 identity + #1541 verifier
                    |
              #1543 adapter
                    |
          #1568 fail-closed junction
                    |
    P0 lifecycle + measurement -----> SC-v0 shadow projection
                 |
        P1 receipts + solver-first
             |       | \
             |       |  +----------> P4 Rule-10 package
             |       +-------------> P2 hex first-hop
             |
       ISO-ORACLE + process sandbox
             |
       P3 one real low-risk growth --> CEGIS-v0 research
             |                            |
             +-------------+--------------+
                           |
                  P5 candidate transition
                           |
               P4 + operator + reversibility
                           |
                    P5 live activation
                           |
                    P6 multi-instance
                           |
                    P7 efficiency/truth

future claim gates (each remains non-authorizing):
  P1 + P4 -------> Panel-2/MAGMA Rule-10 claim gate
  P2 + P4 -------> Panel-1 hex-entry Rule-10 claim gate
  P3 + P4 -------> Panel-3 autonomy Rule-10 claim gate
```

P6 is technically possible after P1, but remains lower priority than P3-P5.
DREAM and GT are side lanes with no outward authority dependency.

## P0 - Production measurement spine

Land the coherent #1540 -> #1541 -> #1543 -> #1568 content stack through
exact-head gates. Treat #1543 as the integration point for the #1540/#1541
content and #1568 as its current leaf; do not merge independent historical
branches twice. Do not enable the dormant served-receipt path until verifier
enforcement is on main.

Implement the claim-window lifecycle as one fail-closed production wrapper:

1. Create a unique window identity per run, or durably invalidate the previous
   clean marker before accepting traffic.
2. Bind a start boundary to the ledger head, source head, normalization
   version, and window ID.
3. Bind every evidence stream to that window. Ledger events, pending-append
   failures, enabled-state observations, and receipt indexes must carry the
   `window_id` or have durable start/end offsets or heads; whole-file or
   historical side-channel scans are not window evidence.
4. Record a successful enabled-state start sample before intake. Capture every
   enable/disable transition and sample at a declared maximum interval so a
   sparse sample cannot stand for the whole window.
5. Cache one emitter for the application lifespan.
6. On shutdown, stop intake and boundedly drain receipt-resolution tasks.
7. Flush the sink before checkpointing the final ledger head.
8. Record a successful end sample, derive its digest, then write the clean
   marker last. Bind that marker to the window ID, start anchor, final ledger
   head, and end-sample digest.
9. Omit the clean marker on any preceding lifecycle or evidence failure,
   including timeout, task failure, transition gap, flush failure, checkpoint
   failure, or end-sample failure. Serving may still fail open.
10. Evaluate only the complete bound start/end segment and verify the sampling
    cadence, all state transitions, and referenced receipt bundles before
    verifying the window for measurement.

The production verifier has two non-authorizing phases. A
`pre_marker_verified` result covers the frozen ledger and every exact
record-cursor slice and permits only writing the marker last. A subsequent
`final_verified` result also re-verifies that marker. Both results are
`measurement_only`, keep `claim_safe_count=0`, and contain no eligibility or
clean-shutdown assertion. Receipt-index rows use
`magma.chat_served_receipt_index_entry.v1`, are window-bound and
content-addressed, and carry only safe relative POSIX manifest references.
`tools/verify_chat_served_production_window.py` exposes the same verifier as a
stable-JSON receiver CLI. It requires receiver-supplied expected window/source
pins and an explicit absolute receiver-owned registry path, and exits nonzero
on any mismatch. A successful invocation is intentionally stateful: it
appends and fsyncs one `final_verified` event while holding the same
cross-process registry lock used during full-marker verification.

A caller-memory list, producer envelope, or caller-supplied list of prior
window IDs is not proof of global freshness. The receiver registry is strict,
bounded, closed-schema, append-only JSONL with a record hash chain and two
states. After pre-marker verification, runtime atomically appends
`reserved_pre_marker`, permanently consuming the exact window ID and binding
the receiver pins plus the proposed marker, its domain-separated canonical
target-path digest, and the full evidence digest. Runtime then re-freezes every
durable input and writes that exact clean marker last. The supported production
finalization entry point is the offline receiver CLI. Its underlying
receiver-owned API remains callback-gated and callable by trusted receiver
code; it is not producer authority or a naked append API. Finalization proceeds
only after re-reading a matching reservation, opening the receiver-pinned
absolute marker target as a no-follow regular single-link file, matching its
canonical bytes to the envelope and reservation, and running full-marker
verification while the registry lock remains held. An envelope marker or
reconstructed copy at a different path is insufficient. A crash after
reservation burns the ID but does not falsely verify it; a missing durable
marker or reservation, second finalization, corrupt registry, lock failure, or
pin mismatch fails closed.

The registry lock/durability contract includes real spawned-process race and
`os._exit` regressions for same-ID reservation, reserve-versus-finalize
serialization, verifier death and death after durable append. A lock-FD close
error cannot strand the process mutex. POSIX reads use nonblocking no-follow
opens so a swapped FIFO or other special file fails closed rather than hanging;
fork children discard inherited sidecar locks and process-local mutex state.
Every successful append syncs the POSIX parent directory, including after a
crash-left empty file, and newly created nested parent entries are anchored in
order. A Windows short-path target that resolves to a different long path after
construction is rejected and must be reconstructed and revalidated.

The registry path is a trust anchor and must be explicitly configured; there
is no relative or implicit default and the CLI cannot create a reservation.
The hash chain detects malformed, reordered, or modified retained rows, but it
cannot prove rollback or valid-tail truncation by an actor with the same
filesystem authority. Production deployment therefore also requires
receiver-controlled ACLs and external backup/anchoring. The feature remains
default-off, `measurement_only`, and `claim_safe_count=0`; this source work
does not authorize a live canary or claim transition.

The API-only startup/shutdown proposal is insufficient: a reused clean marker
can survive an unclean restart, and detached receipt tasks can still be pending
when a checkpoint is written.

**Exit:** a non-empty, dated artifact no older than 14 days; a complete
denominator and enabled-state timeline within the declared maximum sampling
gap; receipt, event, and marker binding re-derive through the final ledger head;
raw content is absent; and `claim_safe_count` remains zero.

**Kill:** missing events disappear from the denominator; a clean marker is
written before drain and flush; a reused window keeps stale eligibility; the
window trusts producer flags; a cross-window or unattributable side-channel
record is accepted; or any automatic claim flip occurs.

## P1 - Default deterministic solver path and per-query MAGMA

Make per-query receipts a config-controlled default with serving fallback and
explicit gap accounting. Make authoritative deterministic solver-first the
default order. Provider or LLM output is never authoritative when an eligible
deterministic solver exists.

Derive both production ratios from the same verified per-served-event receipt
index. Process-local route telemetry remains best-effort operations data and
must not be used as claim evidence.

**Exit:** at least 10,000 served queries with
`served_with_receipt_ratio >= 0.95` and
`solver_first_served_ratio >= 0.95` over one named production window.

The phase exit does not itself flip `claim_safe`. Full provenance, Rule-10,
exact-head consensus, and operator authority still govern stronger claims.

## P2 - Authoritative 8-cell first hop

Measure the current `first_hop_hex_served_ratio`, resolve the naming and
authority difference between the 8-cell solver mesh and 7-cell agent topology,
then make the 8-cell mesh the reversible default served entry.

**Exit:** `first_hop_hex_served_total == served_total` over a named production
window, with receipts and Rule-10 green before any claim flip.

## P3 - One real low-risk autonomous growth

Use one real low-risk domain: production-linked gap, declarative candidate,
independent held-out oracle, process sandbox, shadow evaluation, lifecycle
receipts, and rollback drill. Candidate evaluation is separate from live canary
activation. A candidate may be proposed automatically, but an at-most-5% live
canary requires either a pre-authorized low-risk envelope that explicitly
permits that activation or a head-exact operator gate.

Predeclare a sealed, temporally split held-out pack after the candidate's
generation cutoff with at least 100 cases and at least 20 cases in every named
critical class. Agreement must be `1.0`; with zero failures, the one-sided 95%
upper confidence bound for error must be at most `0.03`.

**Exit:** one real `autonomous_promotions_total` increment inside that authority
envelope; held-out gates green; lifecycle receipt coverage `1.0`; rollback
within one scheduler tick and 60 wall-clock seconds, whichever is stricter;
sandbox violations `0`; and positive marginal verified coverage.

P3 alone cannot move `claim_safe`. The P3 + P4 claim gate, exact-head consensus,
and operator authority remain required for any stronger claim.

Do not add families or domains before this succeeds.

## P4 - Rule-10 safety package

Run this lane in parallel without taking P0/P1 capacity. Mature the
adversarial held-out corpus, make rollback a CI gate, and build the post-cutover
verification harness.

**Exit:** 100% detection on named critical held-out defect classes, rollback
harness green, post-cutover rehearsal green, and no training/held-out leakage.

## P5 - Hex competitive promotion and gated activation

First move one subdivision from shadow to candidate under competitive evidence
and receipts. Live subdivision, ring transport, or `hex_mesh.enabled=true`
requires P4, reversibility, exact-head consensus, and an operator-signed
cutover.

**Candidate exit:**
`shadow_to_candidate_subdivision_transitions_total >= 1`.

**Live exit:** bounded canary, rollback, and post-cutover checks all pass.

## P6 - Multi-instance flywheel

Exchange payload-free signed MAGMA manifests across at least two instances.
Import remains replay metadata and never grants remote or local runtime
authority.

**Exit:** at least one verified two-instance export/import/replay; payload
files `0`; remote mutations `0`; and useful replay improvement reported against
a baseline.

Never translate this into an "infinite scalability" claim.

## P7 - Industrial efficiency and public truth

Publish dated production fallback, latency, audit completeness, resource, and
marginal-coverage scorecards against predeclared budgets. Refresh competitive
evidence and align public wording with machine truth.

**Exit:** budgets green on a named production window, evidence no older than 14
days, and uncited or unbounded public claims removed.

## Transverse lanes

| Lane | First executable seam | Acceptance | Boundary |
|---|---|---|---|
| **SC** | Add `waggledance/core/magma/self_capability.py` after the receipt counter schema stabilizes. Use an immutable reducer over window ID, numerator, denominator, evidence digest, source head, task class, slice, and OOD status. | Byte-stable projection of P0/P1 reports; stale, ineligible, wrong-head, or missing evidence becomes `unknown`. Time-split calibration must beat the base-rate predictor. | SC-v0 stays `shadow=true`, `claim_safe=false`, `authority=none`. Do not alter the current self-model schema or router in v0. Audit the legacy capability-confidence router edge separately. Any later authority proposal is a separate governance change outside SC-v0 and is not authorized here. |
| **ISO-ORACLE** | Versioned held-out packs for the six existing families plus a real process sandbox. | Generator cannot read held-out packs; mutation and metamorphic tests catch defects; hard wall/CPU/RSS/output caps; no network, host writes, or child processes. | `waggledance/core/autonomy/resource_kernel.py` is admission control, not isolation. Generator, executor, and oracle may not share one acceptance defect path. |
| **CEGIS** | Start only after P3; bounded enumeration inside the allowlisted deterministic DSL. | Deterministic convergence within candidate/time/memory bounds; sealed held-out pass; positive gain on a class the incumbent swarm cannot solve. | Candidate output only. No arbitrary Python, imports, I/O, source write, merge, runtime registration, or new authority. |
| **DREAM** | `counterfactual_replay_v0`: deterministic, read-only replay of sanitized failed or uncertain production cases. | Reproducible replay and incremental held-out counterexample or repair-ticket yield over baseline. Each candidate counterexample requires independent reproduction before it can block promotion. | A positive Dream score never promotes and an unreproduced hypothesis never vetoes. Pin production `apply_dream_hints` off until it is proven unable to alter authoritative dispatch. Kill the lane if it adds no held-out value. |
| **GT** | PR #1530 through explicit `SolverServices` opt-in after P1 is staffed. | Exact-head CI, lead + Tools + recognized RCO consensus, explicit operator signature for its Rule-9b class `(a)` paths, hostile iterable bounds, verifier fault injection, and advice-not-dispatch tests. Fable review is advisory. | Advisory only: no controller, connector, automatic MAGMA append, routing precedence, promotion, write, or network authority. Never blocks P0-P3. |

Autorepair stays in the engineering lane. It may draft and test a fix but cannot
auto-merge or change gate/verdict code without protected review. The
meta-learner remains propose-never-enact.

## Authority matrix

| Component | May do | Must never do |
|---|---|---|
| Layer-3 deterministic solver | Produce a bounded result under contract | Grant its own authority |
| SC | Read and project verified counters | Route, promote, mutate, or flip claims |
| Held-out oracle | Accept or refuse candidate behavior | Generate or train the candidate |
| CEGIS | Produce bounded candidate specifications | Install, merge, or activate |
| Dream | Produce hypotheses, candidate counterexamples, and repair tickets | Veto without independent reproduction, receive positive promotion credit, or change live routing |
| Game theory | Return bounded advisory strategy | Dispatch or control external systems |
| MAGMA import | Supply replay metadata | Mutate either instance |
| Repair forge | Draft and test code | Auto-merge or bypass RCO/operator gates |

## Global kill signals

1. Production ratios remain unmeasured while proof tools or solver breadth grow.
2. Missing or gapped traffic disappears from denominators.
3. Advisory components gain route, promotion, write, network, or merge
   authority.
4. Generator, executor, and oracle share the same defect path.
5. Candidate execution lacks hard process isolation.
6. Solver count rises without marginal held-out capability gain.
7. Dream, GT, or CEGIS output is treated as approval.
8. P5 or P6 consumes critical capacity before P0/P1 exits.
9. Any `claim_safe` increase lacks a named window, receipts, Rule-10,
   exact-head consensus, and operator authority.
10. "Consciousness", "emergent superiority", or "infinite scalability" is
    inferred from quantity or synthetic evidence.

## Immediate queue

1. Reconcile #1540/#1541 content at #1543 and preserve #1568 as the coherent
   fail-closed leaf; do not double-land equivalent historical branches.
2. Run CI and exact-head RCO/operator gates on the resulting leaf. Merge only
   if those literal gates match the unchanged head; keep receipts dormant.
3. Finish and review the default-off P0 lifecycle wrapper and independent
   production-window verifier on that coherent leaf.
4. Add receipt-derived P1 counters and authority classification.
5. Start SC-v0 in shadow only after those counter fields are stable.
6. Let #1530 finish independently; it does not satisfy a production phase gate.

Until a production picker is implemented, the lead/operator-owned bridge task
queue schedules unblocked P0/P1 work. `tools/agent_next_task.py` schedules
maintenance, smoke, operational, and Dream side-lane work, but it does not
schedule the P0/P1 production-convergence critical path; Dream tasks use only
surplus capacity.
