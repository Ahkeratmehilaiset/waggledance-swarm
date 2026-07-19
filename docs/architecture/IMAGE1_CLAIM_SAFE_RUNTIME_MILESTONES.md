# WD Image #1 — Claim-Safe Runtime Milestones

**Execution companion:** `WD_PRODUCTION_CONVERGENCE_PLAN_V1.md` is the
canonical phase order. This file remains the claim-language and counter
contract; it is not the backlog.

Status: design spec (measurement-only; defines the language + counter contracts for
flipping `claim_safe` per panel). Produced by **fable-5** for the 100h production sprint
(`wd-production-image1-100h-sprint-20260620`), architecture/synthesis lane. This document
changes **language and counter contracts only** — it flips no claim and adds no
verifier/template/index nesting (respects the sprint freeze).

## 1. The claim-safe principle (applies to every panel)

A panel's literal `image_claim` may flip `claim_safe = true` **only** when ALL hold:

1. **Runtime default, not opt-in/shadow** — the capability is the *default served
   behaviour*, not an opt-in flag, a dry-run, or shadow evidence.
2. **Production-linked coverage counter ≥ threshold over a real window** — a counter
   derived from *served production traffic* (not synthetic) meets a named threshold over a
   named minimum window.
3. **Bound to exact gate receipts** — each counted unit is bound to a MAGMA receipt; the
   verdict is re-derivable from the receipts (no trusting a bare flag).
4. **Rule-10 / Stage-2 gates green** — matured synthetic adversarial corpus +
   proven auto-rollback test + post-cutover verification harness + exact-head consensus +
   operator signature.

Until (1)-(4), `safe_statement` stays the honest current reality and `claim_safe` stays
`False`. **No claim-safe flip from shadow evidence alone.**

The milestone language template:

> `<capability>` is the DEFAULT runtime path for ≥ `N%` of served `<unit>` over a
> ≥ `<window>` production window, each bound to a MAGMA receipt, proven by counter
> `<counter_name>`, with Rule-10 gates green.

## 2. Panel 2 — Deterministic-solver-first + MAGMA default receipts (DEPTH TARGET)

**Current reality:** `SolverRouter` emits a privacy-safe selected-solver trace; the MAGMA
runtime summary receipt is **opt-in**; default full coverage is the open boundary
(`#1301` default-off coverage counters, `#1303` default-off settings-wired receipt sink).

**Claim-safe milestone:**
> For ≥ 95% of served queries over a ≥ 10k-query production window, the route is
> authoritative-solver-first with the LLM strictly advisory, and each served query carries
> a gapless append-only MAGMA receipt — proven by `solver_first_served_ratio` and
> `served_with_receipt_ratio` counters, each re-derivable from the per-query receipt index.

**No-scaffolding capability steps (smallest-first; each flips a default or adds a
production counter — NOT a verifier layer):**

| Step | Change | Counter / gate |
|---|---|---|
| S1 | Land `#1301`/`#1303` (runtime receipt sink + coverage metrics) — foundation, already built | `runtime_receipt_*` metrics exist |
| S2 | Default-ON (config-flagged, safe-fallback) per-query MAGMA receipt bind in the served path; no authority granted | `served_with_receipt_total / served_total` |
| S3 | Make solver-first the **default route order** (authoritative → specialist → LLM advisory) behind a config default; LLM may not answer authoritatively when a solver path exists | `solver_first_served_total / served_total` |
| S4 | Coverage reaches threshold over the window → Rule-10 gates → operator-signed `claim_safe` flip | counters are the truth source; flip is gated, not asserted |

## 3. Panel 1 — Hex-mesh authoritative first-hop (PREREQS IN PARALLEL)

**Current reality:** two topologies exist (8-cell solver-retrieval, 7-cell agent-routing);
route-stage traces are privacy-safe + dashboard-visible.

**Claim-safe milestone:**
> Every served query's FIRST hop is the 8-cell hex-mesh router — proven by
> `first_hop_hex_served_total == served_total` over a production window.

**Prereq steps:** P1a runtime first-hop coverage counter (measure current %); P1b hex-mesh
as the default served entry (config default, safe fallback); P1c Rule-10 prereqs over
routing decisions. Flip only at 100% first-hop served. Sequenced **after** the Panel-2
foundation.

## 4. Remaining panels (milestone language, not this sprint's depth)

| Panel | Claim-safe milestone (counter) |
|---|---|
| MAGMA audit log | Every served query produces an append-only provenance entry verifiable end-to-end (`served_with_provenance_ratio == 1.0`, receipt-chain re-derivable). |
| Low-risk autonomy loop | A solver is promoted by the autonomy loop in production **without human intervention**, gated + auto-rollback-proven (`autonomous_promotions_total ≥ 1` via the gated-real path, Rule-10). Currently dry-run/measurement only. |
| Hexagonal upgrades | A real shadow→candidate subdivision transition occurs under gate (`shadow_to_candidate_subdivision_transitions_total ≥ 1`, currently pinned 0); ring/hierarchy run beyond pure proofs. |
| Future WaggleDance | Aggregate scorecard; downstream of the others — no standalone flip. |

## 5. Anti-sprawl criterion (sprint guardrail)

A sprint slice is a **capability step (allowed)** iff it does at least one of:
- flips a runtime default toward a panel milestone, OR
- adds/advances a *production-linked* coverage counter, OR
- satisfies a *named* Rule-10 / Stage-2 precondition.

A slice is **sprawl (blocked by the freeze)** iff it:
- adds another template / index-entry / verifier / summary layer, OR
- produces `claim_safe=False` shadow evidence without moving a runtime counter.

Current open template/verifier chains (route-stage, MAGMA) are to be **finished/retired to
land**, not extended.
