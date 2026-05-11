# ADR-013 — Reality Check Owner inventory required before adapter proposals

Status: Accepted for EIG2-M0 (Codex peer-review signed 2026-05-11)
Author: Claude (Reality Check Owner, EIG2 Part 12.7)
Peer reviewer: Codex (signed 2026-05-11)
Date: 2026-05-11
R-rule: R13 (Claude addition, agreed in bridge thread `claude-eig2-coldrehearsal-2026-05-11`)

## Context

EIG2 Part 12.7 names Claude as Reality Check Owner. The role is meaningful only if it has a concrete deliverable that gates downstream work. Without an explicit gate, the implementer agent (Codex) can propose adapter shapes against unverified assumptions about existing hooks, recreating the H1+H24-class bug where `agent.domain` was assumed to come from `profiles[0]` (it did not, post-#245).

The audit-fix series demonstrated the cost: 28 H-findings, 25 PRs, ~one week of coordinated multi-agent work. Most of those findings would have been caught earlier with a forced "verify-before-proposing" step.

## Decision

A Reality Check inventory file must exist and be peer-reviewed before any adapter proposal lands. Concretely:

1. RCO produces `docs/eig2/spikes/M0-reality-check.md` containing, for every existing repo hook that EIG2 plans to use:
   - File path and exact line number (anchored to a specific commit SHA).
   - Class or function name.
   - Constructor or primary call signature (verbatim, not paraphrased).
   - Behavior notes that matter for the proposed adapter (e.g., "post-#260 wired via cached_property in container").
2. The inventory MUST cover at minimum:
   - Hex topology selector.
   - MAGMA event/append API.
   - BridgeLLMAdapter constructor (post-#257).
   - AliasRegistry public surface used by `container._resolve_agent_domain` (post-#245).
   - RuntimeGapDetector emission contract (post-#260).
   - ControlPlaneDB read/write surface (post Option B / R22.2e).
   - AgentLifecycleManager.spawn_for_profile (post-#251).
   - HotPathCache miss/hit telemetry hooks.
   - Existing alarm event names (so EIG2 alarms do not collide).
3. The inventory MUST be peer-reviewed by Codex before any Codex-authored adapter proposal (e.g., `.orchestrator/bridge_adapter_spec.md`) is merged.
4. If the inventory is later found incomplete or wrong, the discovering agent posts a bridge message with task_id `eig2-rco-inventory-amendment-<date>`; RCO patches and re-publishes; downstream adapter proposals re-verify their assumptions against the patched inventory.

The inventory is the **anchor of fact** for M0+. Codex cannot proceed to adapter shapes without it; Claude cannot move past M0 without Codex's peer-review on it.

## Alternatives considered

1. **Implicit reality check — agents do their own homework as needed.** Rejected: this is what failed during the audit-fix series. Agents drift onto assumed signatures and only discover errors at integration time. The cost is paid 25× instead of once.
2. **Have Codex write the inventory.** Rejected: Codex's framing bias is verdict-first / proposal-first (per M3 in `docs/eig2/spikes/M0-200-option-summary.md`). The inventory needs open-ended adjacency scan, which is Claude's bias. Codex peer-reviews to catch gaps, but does not author.
3. **Defer inventory until M1.** Rejected: M1 introduces the first runtime touch (`waggledance/core/reasoning/topology_provider.py`); proposing that file's adapter shape without verified hooks is exactly the bug class this ADR prevents.

## Consequences

- **M0 has a defined milestone-internal ordering**: RCO inventory PR1 must merge before Codex's adapter proposal PR2 can.
- **Codex's PR2 may reference the inventory by file:line, not duplicate it.** This keeps the inventory as the single source of truth.
- **RCO has actionable authority**: if Codex's PR2 references a hook the inventory does not list, PR2 is rejected pending an inventory amendment.
- **Inventory is versioned by commit SHA**: amendments produce a new commit; PR2 pins which SHA it verified against.

## Safety impact

Strongly positive. Closes the assumption-drift bug class that produced ~15 of the 28 H-findings in the recent audit cycle.

## Performance impact

Zero direct cost. Indirect benefit: bugs caught at proposal time instead of at integration time → fewer audit-driven rewrites → faster M3/M4 runtime delivery.

## MAGMA invariant impact

None directly. Indirectly protects: by ensuring EIG2 alarm names are checked against existing event vocabulary, prevents accidental shadowing of MAGMA-internal events.

## Audit / regression class

Maps to `INVARIANT_BREAK` (Part 19 RegressionClass enum) if violated: any PR2-class adapter proposal that references a hook NOT present in `docs/eig2/spikes/M0-reality-check.md` (at the SHA the proposal pins) is auto-rejected by the M0 PR gate. The `bridge_classify.py` reference impl (landing in Codex's PR2) must include this regex.

## Reviewed by other agent

Codex reviewed and endorses. The PR #263 re-review already exercised this rule:
Codex found missing `ControlPlaneDB` write methods and profile vocabulary
ambiguity, Claude amended the inventory, and the corrected inventory merged
before PR #268. PR3 adds explicit classifier coverage for unlisted-hook /
inventory-gap language before this ADR lands.

## Related tests

- (existing) `docs/eig2/spikes/M0-reality-check.md` shipped in PR #263 — this ADR ratifies its required structure.
- (existing, PR3) `tests/orchestrator/test_bridge_classify.py::test_rco_inventory_gap_detected_as_invariant_break` — classifier emits `INVARIANT_BREAK` for adapter proposals referencing unlisted hooks.
- (planned, PR3) `tests/contracts/test_eig2_m0_inventory_completeness.py` — fails if M0-reality-check.md lacks any of the nine required sections.

## Provenance

Generalized from R13 binding-rule discussion in bridge thread `claude-eig2-coldrehearsal-2026-05-11` ts `2026-05-11T17:22:51.156126Z`. Pattern: "audit-finding → forward-looking architectural invariant" (M3 meta-insight). Specific past evidence: H1+H24 (PR #245) where agent.domain attribution was assumed wrong; nine of the post-audit Codex-unique items in the 200-option summary cluster around "verify before proposing."

## Date

2026-05-11

## Sign-off

- Author (Claude): signed.
- Peer reviewer (Codex): signed 2026-05-11.
