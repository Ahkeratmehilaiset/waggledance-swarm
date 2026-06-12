# WD Image1 100-Hour PR Sprint

Status: active sprint plan. Created 2026-06-12 by `codex-lead-1`.
Base head: `43f892dd012658602bb7be9c34f3b5a692bdea40`.

This document manifests the Image1 storyboard target as a bounded 100-hour PR
program. It is a queue and review contract, not a claim that the storyboard is
already production-true. The source of truth remains
`tools/wd_image1_capability_manifest.py --json` and
`docs/architecture/WD_IMAGE1_FUNCTIONALITY_MANIFEST.md`.

## Ground Truth

Current capability state at sprint start:

| Image1 capability | Current status | Literal claim safe |
|---|---:|---:|
| Hex-mesh query entry | partial | false |
| Deterministic solver first | partial | false |
| MAGMA audit log | partial | false |
| Low-risk autonomy loop | partial | false |
| Hexagonal upgrades | partial | false |
| Future WaggleDance swarm | partial | false |

The target is progress along the Image1 axis:

`Deterministic First -> Autonomous Growth -> Full Hexagonal Architecture ->
Ring Messaging & Hierarchy -> Emergent Intelligence -> Infinite Scalability ->
Industrial-Grade Efficiency`.

No PR in this sprint may flip a literal storyboard claim to safe unless it
ships production evidence, rollback evidence, and post-cutover verification.

## Guardrails

- Keep PRs small, disjoint, and reviewable.
- Default to proof, verifier, metrics, and path-free summary work before
  runtime authority.
- Keep `RCO_PASS` mandatory for merges.
- Keep Grok as advisory only; if Grok is disabled, record a fresh-head
  `grok_response` bridge event and proceed by lead decision.
- Do not grant autonomous runtime writes through documentation-only or
  template-only PRs.
- Do not convert fast-track, consensus, or queue priority into gate bypass.

## Agent Responsibilities

| Agent | Responsibility | Non-goals |
|---|---|---|
| `codex-lead-1` | Sequencing, claim hygiene, plan PRs, small implementation slices, bridge receipts. | No unilateral runtime cutover. |
| `tools-1` | Verifier tools, tests, CI smoke, benchmark harnesses, failure reproduction. | No merge authority and no silent fallback acceptance. |
| `claude-rco-1` | Primary authority-boundary and security review; `RCO_PASS` or veto. | No production claim promotion without evidence. |
| `claude-rco-2` | Backup adversarial review for identity, rate limit, global ceilings, and cutover reversibility. | No duplicate implementation work unless lead assigns fallback. |
| `fable-5` and builders | Docs, corpus, diagrams, capability map support, fixture-only work. | No runtime authority, no merge authority. |
| `grok-scout-1` | Fresh-head advisory review when enabled. | Advisory output is never a gate pass by itself. |

## 100-Hour PR Queue

| PR | Hours | Owner | Target | Deliverable |
|---:|---:|---|---|---|
| 01 | 0-4 | Lead | Sprint contract | This 100-hour Image1 plan and bridge distribution. |
| 02 | 4-8 | Tools | Baseline counters | CI-backed `wd_image1_capability_manifest` and progress counter smoke receipt. |
| 03 | 8-12 | RCO1 | Authority boundary | Review checklist for all Image1 claim-safe transitions. |
| 04 | 12-16 | Lead | Low-risk autonomy | Runtime-gap scheduler candidate summary renderer, no append or authority. |
| 05 | 16-20 | Tools | Low-risk autonomy | Negative tests for scheduler candidate malformed schema, digest, and path cases. |
| 06 | 20-24 | Lead | Deterministic solver | Per-query solver-trace coverage summary contract, export-only. |
| 07 | 24-28 | Tools | MAGMA | Receipt coverage regression tests and fail-closed missing-sink cases. |
| 08 | 28-32 | RCO2 | MAGMA | Adversarial review of receipt binding and default-sink assumptions. |
| 09 | 32-36 | Lead | Hex upgrades | Shadow subdivision readiness summary renderer, no live mutation. |
| 10 | 36-40 | Tools | Hex upgrades | Parent-child/ring replay fixture verifier and non-finite/path rejection. |
| 11 | 40-44 | Fable | Hex upgrades | Capability map update for subdivision, ring, hierarchy, and Axis B evidence. |
| 12 | 44-48 | RCO1 | Hex upgrades | Cutover veto checklist: rollback, post-cutover verification, and T0c dependency. |
| 13 | 48-52 | Lead | Ring/hierarchy | Message-contract summary for replayed cell messages. |
| 14 | 52-56 | Tools | Ring/hierarchy | Replay transport tests proving no network transport or runtime write side effects. |
| 15 | 56-60 | Lead | V12 counterfactual | Counterfactual-eval bridge summary template, no automatic promotion. |
| 16 | 60-64 | Tools | V12 counterfactual | Regression suite for promotion denial without explicit operator authority. |
| 17 | 64-68 | Fable | Adversarial corpus | Seed corpus expansion docs and fixture index for low-risk solver families. |
| 18 | 68-72 | RCO2 | Adversarial corpus | Red-team review for prompt injection, unsafe provenance, and corpus poisoning. |
| 19 | 72-76 | Lead | Multi-instance flywheel | Sanitized MAGMA replay handoff contract, local-only. |
| 20 | 76-80 | Tools | Multi-instance flywheel | Import/export verifier tests and receipt de-dup checks. |
| 21 | 80-84 | Lead | Efficiency | Budget schema for route depth, contradiction rate, and insight score. |
| 22 | 84-88 | Tools | Efficiency | Benchmark harness smoke and budget fail-closed tests. |
| 23 | 88-92 | RCO1 | Efficiency | Review of benchmark claims and production wording. |
| 24 | 92-96 | Lead | Vision manifest | Refresh Image1 functionality manifest from verified PR evidence only. |
| 25 | 96-100 | Tools + RCO | Sprint close | Aggregate receipts, CI proof, RCO decision, and next 100-hour queue. |

## Definition of Done

- Panel 1 advances only when query-entry evidence shows authoritative first-hop
  mesh behavior or clearly remains candidate/disabled.
- Panel 2 advances when deterministic solver and MAGMA receipt coverage is
  measured, exported, and fail-closed.
- Panel 3 advances when low-risk autonomy evidence is durable, rate-limited,
  operator-visible, and unable to bypass gates.
- Panel 4 advances when subdivision, ring messaging, parent-child hierarchy,
  incremental gap replay, and Axis B have replayable verifiers before cutover.
- Panels 5-6 advance only through bounded scale/efficiency evidence; "infinite"
  and "unlimited" remain unsafe claims.

The first executable slice after this plan is PR 02 unless an RCO veto changes
the order.
