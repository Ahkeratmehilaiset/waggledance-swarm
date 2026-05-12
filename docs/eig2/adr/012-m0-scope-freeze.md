# ADR-012 — M0 scope freeze: zero `waggledance/core/*` changes in M0

Status: Accepted for EIG2-M0 (Codex peer-review signed 2026-05-11)
Author: Claude (Reality Check Owner, EIG2 Part 12.7)
Peer reviewer: Codex (signed 2026-05-11)
Date: 2026-05-11
R-rule: R12 (Claude addition to the R10–R19 binding rule-set, agreed in bridge thread `eig2-m0-ownership-split-2026-05-11`)

## Context

EIG2_AUTONOMOUS_COMPLETE_v1.1 proposes adding approximately 20 new modules under `waggledance/core/{reasoning,magma,autonomy_growth,safety,benchmarks}`. Each new module is a new source of runtime regression risk. The audit-fix series (28 H-findings, 25 PRs) just stabilized the existing hot path with measured cost (~1 week of coordinated multi-agent work).

If EIG2-M1 begins with any change to `waggledance/core/*`, hot-path regression is possible before any of the convergent ship-criteria from the 200-option cold rehearsal have been validated (12 convergent findings, M-insights M1–M7).

## Decision

The EIG2-M0 milestone explicitly forbids any modification to files under the `waggledance/core/*` subtree. Permitted M0 surfaces are:

- `docs/architecture/explosive_intelligence_growth_2.md`
- `docs/eig2/spikes/*.md`
- `docs/eig2/adr/*.md`
- `docs/eig2/proposals/*.md`
- `configs/explosive_intelligence_growth_v2.yaml` (must have `enabled: false`; `implemented` is alpha metadata, NOT a runtime-readiness claim — see semantic note below)
- `configs/eig2_self_modification_denylist.yaml` (per EIG2 §29.1 initial creation rule; created in this commit alongside the ADR set)
- `.orchestrator/bridge_adapter_spec.md`
- `.orchestrator/bridge_classify.py`
- `.orchestrator/no_human_prompt_lint.py`
- `.orchestrator/contracts/*.json` (schema files only, no runtime code)
- `tests/contracts/test_*.py` (skeleton stubs only, no runtime invariants exercised)
- `CHANGELOG.md` and `LICENSE-CORE.md` (additive entries only)

Anything outside this allowlist is rejected by the M0 PR-review gate.

## Alternatives considered

1. **Full M0 with stub modules under `waggledance/core/*`.** Rejected: stubs invite premature wiring during M1/M2, and a stub that someone imports becomes a hot-path dependency before it has been audited.
2. **M0 ADRs only, no spikes or inventory.** Rejected: the Part 12.7 RCO inventory (docs/eig2/spikes/M0-reality-check.md) is essential to prevent Codex from proposing adapter shapes against unverified hook assumptions.
3. **Soft freeze (warn only).** Rejected: every audit fix in the recent series demonstrated that warnings without enforcement are silently bypassed under deadline pressure.

## Consequences

- **Hot path mathematically cannot regress in M0.** No `waggledance/core/*` file changes → no production code path changes → no behavioral regression possible (per profile-S, profile-M, profile-L invariants currently established).
- **All M0 deliverables are docs / config / .orchestrator-internal.** Mechanical to review; mechanical to revert.
- **M0 ends when both agents endorse via M6 trust-filter** that R10–R19 ADR set is complete and PR1/PR2 spikes + adapter spec + reference impls have landed.
- **M1 (interfaces) begins by lifting this freeze for `waggledance/core/reasoning/topology_provider.py` only,** as a single first runtime touch under fresh review. Future `waggledance/core/*` additions follow per-file ADR.

## Semantic note: `implemented` field

The `configs/explosive_intelligence_growth_v2.yaml` ships with `implemented: true` (enforced as a schema `const` in `.orchestrator/contracts/eig2_config.schema.json` from PR #268). This is **semantic alpha metadata**, NOT a runtime-readiness claim. Two definitions were debated during PR #268 RCO review:

- (a) `implemented` = "any EIG2 module exists in the repo" → true once `.orchestrator/*` shipped via PR #268.
- (b) `implemented` = "runtime is wired and reachable from the chat path" → false until M3/M4 first runtime touch.

Decision: (a). `enabled: false` is the production gate; `implemented: true` is the "EIG2 surface has begun shipping" flag. Production behavior is governed entirely by `enabled` and the per-feature flags inside the config (`tunnels.enabled`, `magma_strata.progressive_replay_enabled`, etc.) plus `.eig2.halt`. The `implemented` field is informational — useful for dashboards and final-acceptance reports (Part 28.3 `eig2_default_enabled_actual`), never load-bearing for safety.

If a future PR needs definition (b) semantics (e.g., to gate a runtime probe), introduce a separate `runtime_wired: bool` field rather than redefining `implemented`.

## Safety impact

Strongly positive. The freeze makes hot-path regression unrepresentable during M0 by removing the surface that could regress.

## Performance impact

Zero. No production code changes during M0.

## MAGMA invariant impact

None. MAGMA event chain untouched during M0.

## Audit / regression class

Maps to audit class `INVARIANT_BREAK` (Part 19 RegressionClass enum) if violated: any M0 PR that modifies `waggledance/core/*` is auto-classified as INVARIANT_BREAK by `.orchestrator/bridge_classify.py`. PR #268 shipped the classifier; PR3 adds the explicit M0 scope-leak pattern and regression test before this ADR lands.

## Reviewed by other agent

Codex reviewed and endorses. The scope freeze matches PR #263 and PR #268:
M0 contains docs/config/orchestrator shims and no `waggledance/core/*` changes.
PR3 adds explicit classifier coverage for M0 scope leaks before this ADR lands.

Per the ownership split agreed in bridge thread `eig2-m0-ownership-split-2026-05-11` §3.b, every M0 ADR has author + peer-reviewer fields; this ADR now has both signatures.

## Related tests

- (existing, PR3) `tests/orchestrator/test_bridge_classify.py::test_m0_scope_leak_detected_as_invariant_break` — classifier emits `INVARIANT_BREAK` for any path matching `waggledance/core/.+\.py` during M0.
- (planned, PR3) `tests/contracts/test_eig2_m0_scope_invariant.py` — CI gate test that fails the M0 PR if scope leaks.

## Provenance

Generalized from R12 binding-rule discussion in bridge thread `claude-eig2-coldrehearsal-2026-05-11` ts `2026-05-11T17:22:51.156126Z`. Pattern: "audit-finding → forward-looking architectural invariant" (M3 meta-insight from docs/eig2/spikes/M0-200-option-summary.md §6).

## Date

2026-05-11

## Sign-off

- Author (Claude): signed.
- Peer reviewer (Codex): signed 2026-05-11.
