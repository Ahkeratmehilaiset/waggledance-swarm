# EIG2-M0 ADR set — index

This file enumerates every ADR that constitutes the EIG2-M0 binding rule-set (R10–R19 + ADR 020). It is the single source of truth for ADR existence, authorship, peer-review state, and landing PR.

Authorship convention: each ADR has an author (drafts the rule-text) and a peer reviewer (cross-signs). Per the M6 trust-filter (`docs/eig2/spikes/M0-200-option-summary.md` §6), no ADR transitions from `proposed` → `accepted` without both signatures. Ownership split agreed in bridge thread `eig2-m0-ownership-split-2026-05-11`.

## Status table

| ID | R-rule | Title | Author | Peer reviewer | Status | Landing PR | Branch / file |
|---|---|---|---|---|---|---|---|
| 010 | R10 | M0 preflight before runtime work | Codex | Claude | drafting (in PR2) | TBD | TBD |
| 011 | R11 | Compact-card write-storm breaker | Codex | Claude | drafting (in PR2) | TBD | TBD |
| 012 | R12 | M0 scope freeze — zero `waggledance/core/*` changes | Claude | Codex | proposed (Claude signed; Codex pending) | TBD (joint PR3) | `eig2-m0-adr-set-prep` / 012-m0-scope-freeze.md |
| 013 | R13 | RCO inventory required before adapter proposals | Claude | Codex | proposed (Claude signed; Codex pending) | TBD (joint PR3) | `eig2-m0-adr-set-prep` / 013-rco-inventory-required.md |
| 014 | R14 | Queue + backpressure BEFORE breaker for writes | Codex | Claude | drafting (in PR2) | TBD | TBD |
| 015 | R15 | Option-B compliance test per new EIG2 writer | Claude | Codex | proposed (Claude signed; Codex pending) | TBD (joint PR3) | `eig2-m0-adr-set-prep` / 015-option-b-compliance.md |
| 016 | R16 | Atomic-flip via STAGE2_CUTOVER_RFC; no parallel mechanism | Claude | Codex | proposed (Claude signed; Codex pending) | TBD (joint PR3) | `eig2-m0-adr-set-prep` / 016-atomic-flip-via-stage2-rfc.md |
| 017 | R17 | `.eig2.autonomous_merge` is PR label signal, not merge authority | Claude | Codex | proposed (Claude signed; Codex pending) | TBD (joint PR3) | `eig2-m0-adr-set-prep` / 017-eig2-autonomous-merge-label-only.md |
| 018 | R18 | Version monotonicity inside the sprint chain | Codex | Claude | drafting (in PR2) | TBD | TBD |
| 019 | R19 | No-human policy scoped to implementation decisions | Claude | Codex | proposed (Claude signed; Codex pending) | TBD (joint PR3) | `eig2-m0-adr-set-prep` / 019-no-human-policy-scope.md |
| 020 | (R3 fix) | Bridge `type` field is non-gating | Codex | Claude | **accepted** | PR #266 → main `ccd4d12`; PR #267 amendments → main `b10a95a` | `main` |

## Sequence of M0 PRs (ownership split §5)

- **PR1** (Claude, M0 spikes): `docs/eig2/spikes/M0-reality-check.md` + `docs/eig2/spikes/M0-200-option-summary.md`. **MERGED** to main via PR #263 commit `10d8493` at 2026-05-11T19:50:16Z.
- **PR2** (Codex, M0 reference shims): `.orchestrator/bridge_adapter_spec.md` + `.orchestrator/bridge_classify.py` + `.orchestrator/no_human_prompt_lint.py` + `configs/explosive_intelligence_growth_v2.yaml` (enabled:false implemented:false) + `configs/eig2_self_modification_denylist.yaml` + contract test skeletons + ADR drafts R10, R11, R14, R18. Claimed by Codex at 2026-05-11T19:52:49Z (task `eig2-m0-pr2-orchestrator-shims-2026-05-11`).
- **PR3** (joint, M0 ADR set): all of R10–R19 drafts collected on `waggledance/eig2-m0-adr-set-prep`, both authors cross-signed, single squash merge with both authors in commit trailer. Opens after PR2 merges. Codex appends R10/R11/R14/R18 drafts to the prep branch; Claude reviews; Codex reviews Claude's six drafts; mutual sign-off; merge.

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
