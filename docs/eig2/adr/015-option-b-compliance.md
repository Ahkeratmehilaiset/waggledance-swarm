# ADR-015 — Option-B compliance test required per new EIG2 writer

Status: Accepted for EIG2-M0 (Codex peer-review signed 2026-05-11)
Author: Claude (Reality Check Owner, EIG2 Part 12.7)
Peer reviewer: Codex (signed 2026-05-11)
Date: 2026-05-11
R-rule: R15 (Claude addition, agreed in bridge thread `claude-eig2-coldrehearsal-2026-05-11`)

## Context

The R22.2e "Option B" iteration (shipped 2026-05-11, pre-audit-fix-series CHANGELOG entry) deliberately relaxed read-side locks on `data/control_plane.db` to keep reader p99 stable while writers operate. This trade-off is profile-load-bearing: it works because writer count is bounded and the histogram of write durations uses the corrected wall-clock denominator from PR #224 (where an earlier active-window-only denominator hid pause-time costs and made operator dashboards look healthier than reality).

EIG2 adds many new writers:
- TunnelRegistry persistence (tunnel weight updates, state transitions, decay sweeps).
- CompactDecisionCard creation (every audit-eligible event).
- MAGMA secondary indices for L1/L2/L3 progressive replay.
- Swarm consensus log appends.
- Energy budget tracker accumulators.

Without a per-writer compliance gate, the Option B read-side wins can be neutralized by write storms from new EIG2 tables. This is the bug class Codex flagged as C053+R11 in the 200-option cold rehearsal.

## Decision

Every new EIG2 writer (any new code that produces SQLite writes, MAGMA appends, or storage tier mutations under `waggledance/core/{magma,reasoning,autonomy_growth}/*`) MUST land with a contract test asserting all three:

**(a) Reader non-blocking budget.** Under concurrent writer load at the specified profile's `max_*_rate_per_minute` quota, an existing reader observes its first row within ≤ P milliseconds (P documented per profile in `configs/explosive_intelligence_growth_v2.yaml.resource_quotas`). Reference: profile-small P=50, profile-medium P=20, profile-large P=10. Test uses `tests/contracts/test_option_b_reader_budget.py` style.

**(b) Reader monotonic progress under write storm.** A reader iterating concurrently with a writer storm (≥ 10× normal rate, sustained ≥ 5 seconds) must observe row count strictly increasing in each ≥ 200ms window. No reader stall. Test uses `tests/contracts/test_option_b_reader_monotonic.py` style.

**(c) Wall-clock denominator histogram.** Write-duration metrics MUST use the corrected wall-clock denominator from PR #224 (denominator = `time.monotonic()` over the entire observation window, NOT active-only window). Test asserts the histogram's denominator field equals `wall_clock` and rejects `active_window`. Test uses `tests/contracts/test_wall_clock_denominator.py` style.

This is the **Option-B compliance gate**. Triggered at:
- **M3** for every new MAGMA-adjacent writer (compact card, L1 index, L2 window, L3 hydration-result cache).
- **M4** for every new reasoning-adjacent writer (tunnel registry, swarm consensus log).
- **M5+** for any new autonomy_growth writer.

Bridge_classify.py emits `STORAGE_RESOURCE_ISSUE` for any PR adding a new writer without the three contract tests.

## Alternatives considered

1. **One global Option-B test covering all writers.** Rejected: too coarse to catch per-writer regressions; one slow writer hides under aggregate metrics.
2. **Profile-load tests only at M6 benchmarks.** Rejected: catches the regression at M6 (one large rework) instead of M3 (small fix). Audit-fix-series Pattern: catch at proposal time, not at integration time.
3. **Documentation-only requirement (operator runbook entry).** Rejected: H47 (sync `case_store.save_case`) was documented as a known issue for weeks before it ate p99; documentation without enforcement is silently bypassed.

## Consequences

- Every new EIG2 writer ships with three contract tests.
- M3 and M4 PRs are larger (more tests per change) but cheaper to revert and faster to triage.
- The Option B read-side wins are preserved through EIG2's growth.
- CI cost: each contract test is ~1 second of synthetic write+read traffic. Three tests × ~10 writers expected by M5 ≈ 30 seconds added to required PR gate. Acceptable.

## Safety impact

Strongly positive. Protects the R22.2e gain that took meaningful operator-attention to ship, plus catches the audit class that produced PR #224.

## Performance impact

Direct: zero (tests are off the hot path). Indirect strongly positive: prevents write storms from neutralizing Option B read-side latency wins.

## MAGMA invariant impact

Reinforces append-only invariant: contract test (b) directly verifies reader monotonicity, which is the operational reading of append-only.

## Audit / regression class

Maps to `STORAGE_RESOURCE_ISSUE` (Part 19 RegressionClass enum) on violation. `bridge_classify.py` (PR2) must include regex for the contract-test signatures so a missing-test PR is auto-flagged.

## Reviewed by other agent

Codex reviewed and endorses. The wall-clock denominator requirement is the
right generalization of the PR #224 finding and matches the R11/R14 write
pressure constraints. Runtime writers remain out of M0; this ADR binds M3+.

## Related tests

- (existing) PR #224 fixed the wall-clock denominator; this ADR makes that fix a binding contract.
- (existing) R22.2e Option B reader-relaxation tests under `tests/storage/test_control_plane_option_b_*.py` (verify file paths during M0 PR3 review).
- (planned, M3) `tests/contracts/test_option_b_compact_card_writer.py`.
- (planned, M4) `tests/contracts/test_option_b_tunnel_registry_writer.py`.
- (planned, M5) `tests/contracts/test_option_b_swarm_consensus_writer.py`.

## Provenance

Generalized from R15 binding-rule + Codex's C053 (wall-clock denominator) + Codex's R11 (compact-card write storm). Both agents converged on this in the 200-option exercise; documented in `docs/eig2/spikes/M0-200-option-summary.md` §4 item 6 and §5 item 4.

## Date

2026-05-11

## Sign-off

- Author (Claude): signed.
- Peer reviewer (Codex): signed 2026-05-11.
