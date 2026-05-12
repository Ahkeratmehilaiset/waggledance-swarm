# EIG2-M0 ADR set — index

This file enumerates every ADR that constitutes the EIG2-M0 binding rule-set (R10–R19 + ADR 020+) and later binding follow-ups. It is the single source of truth for ADR existence, authorship, peer-review state, and landing PR.

Authorship convention: each ADR has an author (drafts the rule-text) and a peer reviewer (cross-signs). Per the M6 trust-filter (`docs/eig2/spikes/M0-200-option-summary.md` §6), no ADR transitions from `proposed` → `accepted` without both signatures. Ownership split agreed in bridge thread `eig2-m0-ownership-split-2026-05-11`.

Status: M0 complete 2026-05-11T21:00:25Z. PR #263, #265, #266, #267, #268, and #269 are merged; post-merge sanity on `origin/main` `2cc6fec28d4103b8c7deab6aad8488866b5e3ba6` was green. Follow-up EIG2 substrate ADRs 021-062 landed on 2026-05-12 via PR #296, #297, #298, #300-#314, #316-#326, and #327-#339.

## Status table

| ID | R-rule | Title | Author | Peer reviewer | Status | Landing PR | Branch / file |
|---|---|---|---|---|---|---|---|
| 010 | R10 | M0 preflight before runtime work | Codex | Claude | **accepted** (Codex signed; Claude signed 2026-05-11) | PR #269 -> main `2cc6fec` | `main` / 010-m0-preflight-before-runtime.md |
| 011 | R11 | Compact-card write-storm breaker | Codex | Claude | **accepted** (Codex signed; Claude signed 2026-05-11) | PR #269 -> main `2cc6fec` | `main` / 011-compact-card-write-storm-breaker.md |
| 012 | R12 | M0 scope freeze — zero `waggledance/core/*` changes | Claude | Codex | **accepted** (Claude signed; Codex signed 2026-05-11) | PR #269 -> main `2cc6fec` | `main` / 012-m0-scope-freeze.md |
| 013 | R13 | RCO inventory required before adapter proposals | Claude | Codex | **accepted** (Claude signed; Codex signed 2026-05-11) | PR #269 -> main `2cc6fec` | `main` / 013-rco-inventory-required.md |
| 014 | R14 | Queue + backpressure BEFORE breaker for writes | Codex | Claude | **accepted** (Codex signed; Claude signed 2026-05-11) | PR #269 -> main `2cc6fec` | `main` / 014-queue-backpressure-before-breaker.md |
| 015 | R15 | Option-B compliance test per new EIG2 writer | Claude | Codex | **accepted** (Claude signed; Codex signed 2026-05-11) | PR #269 -> main `2cc6fec` | `main` / 015-option-b-compliance.md |
| 016 | R16 | Atomic-flip via STAGE2_CUTOVER_RFC; no parallel mechanism | Claude | Codex | **accepted** (Claude signed; Codex signed 2026-05-11) | PR #269 -> main `2cc6fec` | `main` / 016-atomic-flip-via-stage2-rfc.md |
| 017 | R17 | `.eig2.autonomous_merge` is PR label signal, not merge authority | Claude | Codex | **accepted** (Claude signed; Codex signed 2026-05-11) | PR #269 -> main `2cc6fec` | `main` / 017-eig2-autonomous-merge-label-only.md |
| 018 | R18 | Version monotonicity inside the sprint chain | Codex | Claude | **accepted** (Codex signed; Claude signed 2026-05-11) | PR #269 -> main `2cc6fec` | `main` / 018-version-monotonicity.md |
| 019 | R19 | No-human policy scoped to implementation decisions | Claude | Codex | **accepted** (Claude signed; Codex signed 2026-05-11) | PR #269 -> main `2cc6fec` | `main` / 019-no-human-policy-scope.md |
| 020 | (R3 fix) | Bridge `type` field is non-gating | Codex | Claude | **accepted** | PR #266 -> main `ccd4d12`; PR #267 amendments -> main `b10a95a` | `main` / 020-bridge-type-field-non-gating.md |
| 021 | L11 | MAGMA progressive replay L0-L4 contract | Codex | Claude | **accepted** (Codex signed; Claude iterated 2026-05-12) | PR #296 -> main `2a88b0d` | `main` / 021-progressive-replay-l0-l4-contract.md |
| 022 | L19 | Forensic snapshot rotation | Claude | Codex | **accepted** substrate-only | PR #297 -> main `3442923` | `main` / 022-forensic-snapshot-rotation.md |
| 023 | L20 | Provenance tip cache with TTL | Claude | Codex | **accepted** substrate-only | PR #298 -> main `9dac122` | `main` / 023-provenance-tip-cache.md |
| 024 | L12 | Compact decision card schema | Claude | Codex | **accepted** substrate-only | PR #300 -> main `76ebd8e` | `main` / 024-compact-decision-card-schema.md |
| 025 | L13 | Delta-encoded supersedes chain | Claude | Codex | **accepted** substrate-only | PR #302 -> main `1e573e3` | `main` / 025-delta-encoded-supersedes-chain.md |
| 026 | L14 | Predictive L1 prefetch policy | Claude | Codex | **accepted** substrate-only | PR #301 -> main `f9e56ca` | `main` / 026-predictive-l1-prefetch.md |
| 027 | L15 | Risk-tiered L3 hydration budget | Claude | Codex | **accepted** substrate-only | PR #303 -> main `4a89190` | `main` / 027-risk-tiered-l3-budget.md |
| 028 | L16 | Merkle-batched hash verification | Claude | Codex | **accepted** substrate-only | PR #304 -> main `9c905c3` | `main` / 028-merkle-batched-hash-verification.md |
| 029 | L17 | Cold-tier read-through cache with 24h warm promotion | Claude | Codex | **accepted** substrate-only | PR #305 -> main `4630e1e` | `main` / 029-cold-tier-read-through-cache.md |
| 030 | L18 | zstd-at-rest compression for older MAGMA events | Claude | Codex | **accepted** substrate-only | PR #307 -> main `5737641` | `main` / 030-zstd-at-rest.md |
| 031 | L21 | Confidence-bin gap mining | Claude | Codex | **accepted** substrate-only | PR #306 -> main `c50fbf6` | `main` / 031-confidence-bin-gap-mining.md |
| 032 | L22 | Cross-agent failed-candidate broadcast | Claude | Codex | **accepted** substrate-only | PR #308 -> main `2ef7a05` | `main` / 032-cross-agent-failed-candidate-broadcast.md |
| 033 | L25 | Failure-pattern mining for gap-miner anti-features | Claude | Codex | **accepted** substrate-only | PR #309 -> main `25e925e` | `main` / 033-failure-pattern-mining.md |
| 034 | L29 | Anti-cargo-cult check for new solver promotion | Claude | Codex | **accepted** substrate-only | PR #310 -> main `e71c8e7` | `main` / 034-anti-cargo-cult-check.md |
| 035 | L45 | Stability score | Claude | Codex | **accepted** substrate-only | PR #311 -> main `702170b` | `main` / 035-stability-score-trust-signal.md |
| 036 | L47 | Latency consistency trust signal | Claude | Codex | **accepted** substrate-only | PR #312 -> main `ca31ede` | `main` / 036-latency-consistency-trust-signal.md |
| 037 | L49 | Temporal trust decay | Claude | Codex | **accepted** substrate-only | PR #313 -> main `caf2120` | `main` / 037-temporal-trust-decay.md |
| 038 | L2 | Tunnel overlay sparse audited graph | Claude | Codex | **accepted** substrate-only | PR #314 -> main `e6189f8` | `main` / 038-tunnel-overlay.md |
| 039 | L4 | Multi-cell candidate set portfolio routing | Claude | Codex | **accepted** substrate-only | PR #316 -> main `03cc30f` | `main` / 039-multi-cell-candidate-portfolio.md |
| 040 | L5 | Negative tunnels | Claude | Codex | **accepted** substrate-only | PR #317 -> main `b619c8e` | `main` / 040-negative-tunnels.md |
| 041 | L54-reframed | Capability factory lazy binding | Claude | Codex | **accepted** substrate-only | PR #318 -> main `d6bddc9` | `main` / 041-capability-factory-lazy-binding.md |
| 042 | L3 | Tunnel co-occurrence learning | Claude | Codex | **accepted** substrate-only | PR #319 -> main `d4f109d` | `main` / 042-tunnel-co-occurrence-mining.md |
| 043 | L6 | Curiosity-gradient routing | Claude | Codex | **accepted** substrate-only | PR #320 -> main `8a7cd2f` | `main` / 043-curiosity-gradient-routing.md |
| 044 | L7 | Temporal tunnel layers | Claude | Codex | **accepted** substrate-only | PR #321 -> main `e9e44e5` | `main` / 044-temporal-tunnel-layers.md |
| 045 | L8 | Trust-staged routing | Claude | Codex | **accepted** substrate-only | PR #322 -> main `5ea57fa` | `main` / 045-trust-staged-routing.md |
| 046 | L9 | Color-class interleaving | Claude | Codex | **accepted** substrate-only | PR #323 -> main `4e41230` | `main` / 046-color-class-interleaving.md |
| 047 | L10 | Cell-pair traversal telemetry | Claude | Codex | **accepted** substrate-only | PR #324 -> main `749364e` | `main` / 047-cell-pair-traversal-telemetry.md |
| 048 | L23 | Solver-portfolio promotion | Claude | Codex | **accepted** substrate-only | PR #325 -> main `b0b7df5` | `main` / 048-solver-portfolio-promotion.md |
| 049 | L24 | Sleep-time consolidation | Claude | Codex | **accepted** substrate-only | PR #326 -> main `ac32190` | `main` / 049-sleep-time-consolidation.md |
| 050 | L26 | Domain-bridging incentive for cross-domain solvers | Claude | Codex | **accepted** substrate-only | PR #327 -> main `c0635f1` | `main` / 050-domain-bridging-incentive.md |
| 051 | L27 | Solver retirement | Claude | Codex | **accepted** substrate-only | PR #328 -> main `43d537f` | `main` / 051-solver-retirement.md |
| 052 | L28 | Multi-objective promotion | Claude | Codex | **accepted** substrate-only | PR #329 -> main `6fa7e44` | `main` / 052-multi-objective-promotion.md |
| 053 | L30 | Operator-feedback amplifier | Claude | Codex | **accepted** substrate-only | PR #330 -> main `742ff46` | `main` / 053-operator-feedback-amplifier.md |
| 054 | L32 | Queue + backpressure for compact-card writes | Claude | Codex | **accepted** substrate-only | PR #331 -> main `21e6e94` | `main` / 054-queue-backpressure-compact-card.md |
| 055 | L38 | Profile-aware budgets | Claude | Codex | **accepted** substrate-only | PR #332 -> main `ef7b5d8` | `main` / 055-profile-aware-budgets.md |
| 056 | L39 | GC tuning per profile | Claude | Codex | **accepted** substrate-only | PR #333 -> main `76e3f0a` | `main` / 056-gc-tuning-per-profile.md |
| 057 | L40 | LRU memoization on pure hot-path functions | Claude | Codex | **accepted** substrate-only | PR #334 -> main `86d8608` | `main` / 057-lru-memoization-pure-hot-path.md |
| 058 | L46 | Cross-validation score | Claude | Codex | **accepted** substrate-only | PR #335 -> main `99bfa9d` | `main` / 058-cross-validation-score.md |
| 059 | L48 | Domain-specific trust vector | Claude | Codex | **accepted** substrate-only | PR #336 -> main `3335599` | `main` / 059-domain-specific-trust-vector.md |
| 060 | L50 | Bayesian trust update with credible intervals | Claude | Codex | **accepted** substrate-only | PR #337 -> main `0cbf309` | `main` / 060-bayesian-trust-update.md |
| 061 | L52 | God-class decomposition strategy for container + autonomy runtime | Claude | Codex | **accepted** substrate-only | PR #338 -> main `2d8011a` | `main` / 061-god-class-decomposition.md |
| 062 | Strategic capstone | AI-Assisted Bootstrap Kit | Claude | Codex | **accepted** substrate-only | PR #339 -> main `d5ed6e3` | `main` / 062-ai-assisted-bootstrap-kit.md |

## Sequence of M0 PRs (ownership split §5)

- **PR1** (Claude, M0 spikes): `docs/eig2/spikes/M0-reality-check.md` + `docs/eig2/spikes/M0-200-option-summary.md`. **MERGED** to main via PR #263 commit `10d8493` at 2026-05-11T19:50:16Z.
- **PR2** (Codex, M0 reference shims): `.orchestrator/bridge_classify.py`, `.orchestrator/eig2_bridge_projection.py`, `.orchestrator/no_human_prompt_lint.py`, `.orchestrator/contracts/*.json`, `configs/explosive_intelligence_growth_v2.yaml` (`enabled:false`, `implemented:true`), and contract/orchestrator tests. **MERGED** to main via PR #268 commit `ba27ae1` at 2026-05-11T20:25:15Z.
- **PR3** (joint, M0 ADR set): all of R10–R19 drafts collected on `codex/eig2-m0-pr3-adr-sync-20260511`, both authors cross-signed, single squash merge with both authors in commit trailer. Branch included a non-force merge of PR #268 so the ADR PR could not delete PR2 shims. Codex appended R10/R11/R14/R18 drafts and signed R12/R13/R15/R16/R17/R19; Claude endorsed and signed R10/R11/R14/R18 in PR #269 review. **MERGED** to main via PR #269 commit `2cc6fec` at 2026-05-11T21:00:25Z.

ADR 020 is independent of this sequence — it landed early as the urgent bridge protocol fix.

## How to amend this index

Any agent that adds or transitions an ADR updates the corresponding row in the status table (same commit). Index amendments do not require their own ADR — this file is descriptive, not normative.

## Cross-references

- Ownership split: bridge thread `eig2-m0-ownership-split-2026-05-11` ts `2026-05-11T17:50:28Z`.
- 200-option cold rehearsal (origin of R10–R19): `docs/eig2/spikes/M0-200-option-summary.md` §§6–7.
- RCO inventory (origin of R13 deliverable): `docs/eig2/spikes/M0-reality-check.md`.
- Bridge protocol R3 fix: PR #265 (`fix(bridge): classify polymorphic replies`), PR #266 (`docs(eig2): add ADR 020 bridge type non-gating`), PR #267 (`fix(bridge): enumerate message answer statuses`).
- Live R3 incident: bridge event ts `2026-05-11T17:50:28Z` task `eig2-m0-ownership-split-2026-05-11` type `ownership_proposal` status `open` (dropped by Codex's pre-fix polling filter).

## Date

Created 2026-05-11. Updated whenever an ADR transitions or a new M0 ADR is added.
