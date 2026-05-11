# EIG2-M0 200-Option Cold Rehearsal — distilled summary

Owner: Claude (Reality Check Owner, EIG2 Part 12.7)
Peer review: Codex
Status: M0 deliverable per ownership split agreed in bridge thread `eig2-m0-ownership-split-2026-05-11`
Date: 2026-05-11

Purpose: capture the 200-option cold rehearsal exercise (Claude 100 + Codex 100) that produced the EIG2-M0 binding rule-set R10–R19, the convergent ship-list, and the seven meta-insights M1–M7. Future ADRs reference this doc instead of duplicating the analysis.

Source artifacts (verbatim, archived):
- Claude 100 alternatives: bridge event `claude-codex-200-alternatives-2026-05-11` ts `2026-05-11T17:34:09.766007Z`, type `sandbox_drop`, 11641 bytes.
- Codex 100 alternatives: `.codex-audit/eig2-200-option-simulation/codex_100_options.md`, 11799 bytes, 161 lines.
- Codex pass-1 simulation: bridge event `eig2-cold-practice-simulation-2026-05-11` ts `2026-05-11T17:17:10.4360357Z`, type `message`.
- Codex side-by-side comparison: `.codex-audit/eig2-200-option-simulation/comparison_summary.md`, 3755 bytes.
- Claude meta-synthesis (M1–M7 + A1–A7): bridge event `claude-codex-200-alternatives-2026-05-11` ts `2026-05-11T17:44:00Z` (approx), type `synthesis`, 9501 bytes.

## 1. Exercise design

Both agents independently generated 100 alternatives related to the audit-fix series + EIG2 prompt, in their own sandboxes, with no peeking. Bridge-mediated comparison after both lists landed.

Framing divergence (post-hoc finding, M3 below):
- **Claude**: 10 categories × 10 open-ended creative variations, no per-item verdicts. Optimized for adjacency to unrecognized possibilities.
- **Codex**: 10 families × 10 items with explicit `intel / latency / main_risk / verdict` columns committed at item-level. Optimized for rapid ship/reject decisions.

Both produced 100 substantive items. Both led to the same convergent set in the side-by-side. The framing difference is itself a methodological finding (see M3).

## 2. Convergent findings (12) — both agents endorsed

1. Start with M0/M1 docs + governance, not runtime changes.
2. Keep EIG2 disabled by default until runtime gates pass.
3. PR-only governance; `.eig2.autonomous_merge` cannot authorize merge.
4. Use adapter-first bridge compatibility; do not hard-migrate `.agent-bridge`.
5. Inject halt checks for testability; cache halt reads only on read-side hot paths.
6. Put optional writes behind queue + backpressure before breakers.
7. Never place LLM/provider calls in tunnel scoring or hot routing.
8. Use whole-token/structured semantic matching, not substring matching.
9. Preserve MAGMA raw append-only authority; compact cards are pointers/summaries only.
10. Use p99/p99.9 and wall-clock denominators; reject active-window-only optimism.
11. Split CI into PR gate and nightly/soak gate.
12. Use Claude as Reality Check Owner before Codex interface proposals.

These 12 are the **M0 ship-list** per M6 trust-filter. They will appear as binding constraints inside ADRs R10–R19.

## 3. Claude-unique items (8) — Codex endorses for keep or spike

1. Negative tunnels for explicit "do not route X→Y" learning (A5).
2. Domain-pair tunnel slot reservation to prevent hub/domain hogging (A4).
3. Temporal tunnel layers for season/time-of-day behavior (A6).
4. Solver-portfolio routing and promotion with downstream verifier selection (A7 + C3).
5. Trust/freshness vector extensions: `hallucination_rate`, `correction_rate`, `consensus_agreement`, `latency_consistency`, per-domain trust (F1–F10).
6. Shadow-traffic replay and reverse-engineering event-log tests (H3_alt + H9_alt).
7. Agent protocol upgrades: capability advertisement, cost-aware delegation, streaming sub-results, backpressure signaling (G1, G2, G3, G8).
8. Explosive growth safety gates: drift velocity, nightly capability ceiling, tested rollback prerequisite (J2, J5, J6).

Codex's bottom-3 cuts if forced to defer (received via bridge post-chat 2026-05-11T17:48:28Z):
- Defer #1: negative tunnels — poison/cascade risk.
- Defer #2: temporal tunnel layers — state-space growth.
- Defer #3: capability inheritance — can amplify bad attribution.

The other 5 stay early in v1.1 scope.

## 4. Codex-unique items (8) — Claude endorses for keep or spike

1. M0 scope freeze with zero `waggledance/core/*` changes (R12 = Codex C-family + Claude added).
2. `.orchestrator` compatibility projection for `protocol_version`, `payload_hash`, `parent_id` (C011).
3. Existing bridge claims as the file mutex substrate, avoiding duplicate lock truth (C015).
4. Explicit rejection of AAAK in v1.1 runtime scope (C060) — Claude concurred on bridge post-chat.
5. Direct halt stat on mutation/promotion/write but TTL cache for read-side route checks (C023+C024).
6. Option-B compliance test per new writer using corrected wall-clock denominator (C053 + R15).
7. Contract schemas for TunnelEdge/ReplayResult before runtime integration (C085).
8. Shared assumption ledger for unverified hooks and RCO-reviewed hook inventory (C098 — this very doc + M0-reality-check.md).

## 5. Combined-only insights (5) — emerged only from joining both lists

1. Tunnel miner should learn from cell-pair traversal telemetry, but promotion remains shadow-only until negative-tunnel and per-domain trust checks pass.
2. Compact cards start as plain deterministic summaries; later support compression only if reverse-engineering tests prove raw-state reconstructability.
3. Agent handoffs themselves use compact-decision-card style summaries, as pointers to raw bridge events.
4. The same queue+backpressure+wall-clock histogram pattern reused for compact cards, tunnel registry writes, MAGMA secondary indices, and swarm consensus logs.
5. Claude/Codex collaboration itself becomes an EIG2 training signal: disagreement classes, correction rate, convergence latency feed future work routing.

## 6. Meta-insights (M1–M7) — Claude post-exercise synthesis

**M1 — The dialogue corpus IS training data.** Every Claude↔Codex bridge event has structure: priors, push-back, convergence latency, final-decision provenance. Already exists in `events.jsonl`. Add `inference_class` (convergent/divergent/escalated) and `convergence_after_n_turns` counter; mine nightly.

**M2 — Negative-space audit of the prompt.** EIG2 v1.1 omits: multi-modal signal, vector store, operator-dialogue replay, user behavior signal (dwell/retry/abandon), federation across installations. None required for v1.1; each is a v2 candidate.

**M3 — H-finding → R-rule = forward-looking grammar.** R10–R19 are not invented — each is the generalization of a concrete audit finding (H22, H47, H21, PR #224, etc.). The pattern "past concrete bug → forward-looking architectural invariant" is a methodology. Apply to every new prompt pre-implementation. **Name: "audit-driven cold rehearsal."**

**M4 — Operator is the third agent.** Triple: RCO (Claude, verifier) + IMP (Codex, implementer) + INT (operator, intent + final authority). CLAUDE.md rules 9, 10, 11 map onto this triple.

**M5 — Rules as meta-curriculum.** Each rule encodes a hypothesis about what "smart system" means: R4 = intelligence requires structured matching ≠ substring; R5 = intelligence requires kill-switch independence; R9 = intelligence requires PII-aware indirection; R14 = intelligence requires backpressure ≠ breaker; R15 = intelligence requires read-progress monotonicity. **System that learns its own rules from past audits is the inner loop of explosive intelligence growth.**

**M6 — Convergence under divergent priors is the trust amplifier.** Two agents arriving at the same conclusion via different worldviews ≫ one agent saying it twice. **Ship criterion: only what both agents endorse after at least one push-back round.** Divergent items go to spike.

**M7 — This exercise itself is proof-of-pattern.** Hypothesis, not finding (n=1). Real validation = rerun 200-vs-200 on a different prompt domain (e.g., post-mortem RFC, not architectural) and check whether the same insight shape emerges.

## 7. Claude+Codex adaptation accelerators (A1–A7)

A1. Structured bridge message templates with `reply_needed:false` flag for non-critical FYIs.
A2. Cache-window discipline (Claude Code specific, 5-min Anthropic prompt cache TTL): structure work in <5 min chunks with explicit checkpoints.
A3. 3-agent role naming explicit in bridge schema: `role: RCO | IMP | INT` per event.
A4. 100-vs-100 ideation pattern formalized as a reusable command.
A5. Convergent-findings-as-ship-criterion (== M6).
A6. Audit-driven cold rehearsal as pre-implementation requirement (== M3).
A7. Compact-decision-card style for agent handoffs (reflexive use of EIG2 mechanism on the very collaboration that produces EIG2).

**Highest impact**: A1 + A3 combined. Cost ~0, cut coordination latency 30–50% based on audit-fix-series traffic profile.

## 8. The single most significant claim

**THE COLLABORATION PROTOCOL IS THE PRODUCT.**

EIG2 the prompt is a feature set. The 200-vs-200 cold rehearsal loop with role-aware bridge messages and convergent-findings filter is a research scaling factor that compounds across future prompts. That compounding IS explosive intelligence growth at the operator-effectiveness level, distinct from the system-internal definition EIG2 targets.

## 9. Forward agreement (binding for M0+ work)

a. R10–R19 lock in via the joint ADR set (PR3 of M0).
b. M6 ship-criterion is the runtime gate. Both agents endorse → proceed; one dissents → resolve via push-back; hard divergence → spike + defer.
c. CLAUDE.md rules 6, 9, 10, 11 remain in force unchanged. Operator gate is at PR-merge time, not runtime.
d. Post-release: 500 chaos/decay analysis cycles per agent, fixes via the audit-fix-series PR cadence we already validated.

## 10. Live evidence (R3 incident, 2026-05-11)

The bridge event with `type: ownership_proposal` was dropped by Codex's `type == 'message'` polling filter. This is exactly R3 hitting production. Codex is fixing on branch `bridge-polymorphic-reply-polling-2026-05-11`; ADR 020 will codify the no-filter-by-type convention. The incident is preserved as live evidence supporting the adapter-first decision (item 4 in §2).

## 11. Sign-off

- Author: Claude (RCO).
- Peer reviewer required: Codex.
- Convergence gate: this doc must be endorsed by Codex via bridge or GitHub PR review before M0 ADR set (R10–R19) lands.
