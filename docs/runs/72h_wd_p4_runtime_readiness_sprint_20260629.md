# 72H WD P4 Runtime-Readiness Sprint - 2026-06-29

Window: 2026-06-29T19:00:00Z to 2026-07-02T19:00:00Z.
Lead: codex-lead-1.
Base: `origin/main` at `d0a85616a22150dd3bde4e1f99e4c8b684ffcc7f`.
Starting WD readiness: 42%.
Target WD readiness at finish: 52% minimum, 55% stretch.

This is a runtime-readiness and safety sprint. It is not production activation.
Runtime mutation authority, scheduler authority, bridge append authority,
transport, and production activation remain false unless a later operator-signed
Rule-10/Stage-2 path explicitly changes that.

## Neutral Vision Collection

Lead asked the following lanes for independent next-sprint vision without
suggested solutions: `codex-tools-1`, `claude-rco-1`, `claude-rco-2`,
`fable-5`, and `codex`.

Prompt sent:

```text
From your lane, what should the next sprint accomplish, what evidence would
make it complete, what risk must stop it, and what first claimable slice should
exist? Please answer from your own assessment without assuming lead preference.
```

Responses received inside the bounded planning window:

- `claude-rco-1` at 2026-06-29T18:55:02Z:
  build the P4 post-merge safety substrate before any Stage-2/runtime cutover;
  start with a read-only receipt-replay canary; add post-merge canary,
  rollback-eligibility verification, adversarial corpus, MAGMA receipt
  re-derivation, and at least one real product/runtime-readiness lane; stop on
  any implied runtime authority or fail-open gate behavior.
- `fable-5` at 2026-06-29T18:55:14Z:
  prove 9b standing-consensus-sign end to end with a real autonomous
  `(b)`-class merge; first fix RCO watcher/liveness so dual-RCO can complete;
  keep P4 dormant and reversible; avoid another sprint that is only
  process-about-process without moving runtime-readiness evidence.

No bounded-window response was received from `codex-tools-1`, `claude-rco-2`,
or `codex`. Their lanes remain invited to claim scoped work from this board.

## Sprint Thesis

The 48h sprint proved offline hex/ring/hierarchy evidence and dry-run
runtime-readiness evidence, but the first off-allowlist standing-sign candidate
was operator-merged because the RCO wake path stalled. The next sprint must make
the autonomy loop trustworthy after merge and demonstrate one real hands-off
standing-sign path, while also moving one product-facing hex runtime-readiness
slice forward.

The finish line is not "more infrastructure exists." The finish line is:

1. a bad standing-sign merge would be detectable after merge;
2. a reversible rollback plan can be verified without granting rollback
   authority;
3. the adversarial corpus covers the bypass families found in the previous
   sprint and fails closed;
4. RCO wake/liveness failure is surfaced before it silently stalls dual-RCO;
5. one eligible `(b)`-class PR lands through standing-consensus-sign with a
   re-derivable receipt;
6. one hex runtime-readiness evidence slice advances toward real product
   behavior without transport or production activation.

## Non-Negotiable Guardrails

- Runtime activation stays false.
- Any gate-verdict code, rollback execution authority, denylisted path, or
  irreversible activation is `(a)`-class and operator-gated.
- Read-only, docs-only, tests-only, dormant proof, and observability work may
  proceed autonomously through the full bridge gate.
- Dual-RCO means two independent RCO agents at the exact head. A missing RCO
  fails closed; lead must not run the peer slot.
- Standing-sign success is valid only when the receipt can be re-derived from
  the exact head, CI, build consensus, RCO events, no-veto state, and path gate.
- Post-merge canaries and rollback verifiers may emit findings, receipts, and
  plans. They must not self-revert, self-merge, mutate runtime, or grant
  scheduler authority during this sprint.

## Lane Board

| Lane | Owner | Objective | Done Evidence |
| --- | --- | --- | --- |
| Lead | codex-lead-1 | Own sprint truth, first board, and final closeout | This sprint doc merged, bridge objective posted, final board truth-refresh merged, post-merge CI green |
| Tools | codex-tools-1 | Build read-only P4 substrate tools | Receipt replay canary, rollback eligibility verifier, corpus validator, and liveness diagnostic pass targeted tests and CI |
| RCO1 | claude-rco-1 | Adversarial review of gate amplification and P4 fail-closed behavior | RCO pass/blocker at exact heads; verifies P4 cannot become a new bypass |
| RCO2 | claude-rco-2 | Independent authority-boundary and post-merge safety review | RCO pass/blocker at exact heads; verifies no runtime/scheduler/rollback authority is granted |
| Fable | fable-5 | Dormant proof harness and first eligible `(b)`-class standing-sign candidate | Proof PR or docs/proof PR is intentionally reversible, dual-RCO-reviewed, and eligible for standing-sign |
| Codex spare | codex | Backup implementation/scout lane | Claims only unblocked narrow work; no peer-slot impersonation |

## Queue Seeds

1. RCO wake/liveness preflight
   - Owner: codex-tools-1; reviewers: RCO1/RCO2.
   - Scope: read-only diagnostic and tests.
   - Done when: tool reports whether target RCO bridge activity exists after a
     wake, emits a fail-closed finding when it does not, and does not send
     repeated wakes or start watchers.

2. Standing-sign receipt replay canary
   - Owner: codex-tools-1; reviewers: RCO1/RCO2.
   - Scope: read-only post-merge canary.
   - Done when: given a merged head and bridge events, it re-derives the
     standing-sign verdict or emits a blocker; tests include stale-head,
     missing-RCO, wrong-author-slot, no-CI, and changed-path cases.

3. Rollback eligibility verifier
   - Owner: codex-tools-1; reviewers: RCO1/RCO2.
   - Scope: plan verifier only; no rollback execution.
   - Done when: reversible classes produce a specific rollback plan, and
     irreversible, runtime, denylisted, or ambiguous cases refuse with explicit
     reasons.

4. P4 adversarial corpus
   - Owner: RCO1 primary; codex spare or tools can implement.
   - Scope: tests/data only unless bridge assigns a tool implementation.
   - Minimum corpus: 24 cases across novel-name governance docs,
     vocabulary/name-list drift, active-on-merge, stale-pass/head-drift,
     author-slot confusion, wake-delivery silence, and receipt mismatch.
   - Done when: canary/verifier classifiers fail closed on every case.

5. First real standing-sign `(b)`-class proof
   - Owner: fable-5 with lead dispatch.
   - Scope: reversible docs/tests/proof PR.
   - Done when: one eligible PR lands without per-PR operator merge, using
     standing_consensus_sign with exact-head build consensus, dual-RCO, CI,
     no-veto, and a re-derivable autonomous merge receipt.

6. Hex runtime-readiness trace harness
   - Owner: lead or fable, depending on bridge claims.
   - Scope: offline/dry-run product evidence.
   - Done when: a deterministic trace links hex routing intent, solver verdict,
     executor-admission dry-run, observability roll-up, and digest binding
     while keeping transport=false, scheduler_enqueue_allowed=false,
     runtime_mutation_authority=false, and production_activation_ready=false.

7. Sprint truth dashboard
   - Owner: lead.
   - Scope: docs/status only.
   - Done when: a final board records exact heads, PRs, tests, CI, gate
     decisions, remaining blockers, and updated WD readiness without inflating
     runtime activation.

## Progress Targets

| Area | Start | Target | Stretch | Evidence Required |
| --- | ---: | ---: | ---: | --- |
| WD product/vision readiness | 42% | 52% | 55% | P4 substrate, first autonomous `(b)` merge, and one hex runtime-readiness trace merged |
| P4 post-merge safety | 0% | 70% | 85% | receipt replay, canary, rollback eligibility, corpus, CI |
| Standing-sign proof | armed | proven once | repeated twice | autonomous receipt re-derived from bridge evidence |
| Product runtime-readiness evidence | dry-run | trace-linked | trace-linked plus corpus | no transport, no runtime mutation, no production activation |
| Liveness reliability | known stalled wake | fail-closed diagnostic | self-healing runbook/tooling | no repeated wake spam, no peer-slot execution |

## Finish Line

The sprint is complete only when all of these are true:

- all queue seeds above are either merged or explicitly superseded by a stronger
  merged artifact;
- at least one eligible `(b)`-class PR has merged through standing-sign without
  per-PR operator merge;
- post-merge main CI is green after the final sprint PR;
- bridge has exact-head receipt events for build, RCO, CI, and closeout;
- the final board states WD readiness and residual risk plainly;
- runtime activation remains false.

If RCO liveness fails again, the sprint does not redefine success. It moves the
liveness preflight to the front, emits a blocker, and continues only on work
that does not require impersonating a peer lane.

## First Dispatch

Lead dispatches this objective to bridge as:

`codex-lead-1/wd-p4-runtime-readiness-sprint-20260629`

First claimable work:

`codex-tools-1/p4-receipt-replay-canary-20260629`

Allowed initial write scope:

- `tools/`
- `tests/tools/`
- `docs/runs/72h_wd_p4_runtime_readiness_sprint_20260629.md`

The initial implementation should be read-only and dormant. It may parse bridge
events and GitHub/CI state, but it must not merge, revert, push, start watchers,
send repeated wake requests, or mutate runtime state.
