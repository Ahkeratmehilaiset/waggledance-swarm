# ADR-021 - MAGMA progressive replay L0-L4 contract

Status: Accepted for EIG2-M0 follow-up (Codex authored 2026-05-12;
Claude iteration requested post-merge)
Author: Codex
Peer reviewer: Claude (requested 2026-05-12)
Date: 2026-05-12
Leap: L11

## Context

L11 proposes MAGMA progressive replay strata so runtime boot does not perform
full raw replay as MAGMA grows. The target shape is:

- L0 BootHeader, 128 tokens, boot-time;
- L1 compact cards, 512 tokens, boot-time top-K;
- L2 local replay window, 2048 tokens, lazy;
- L3 selective hydration, 8192 tokens, on demand;
- L4 forensic replay, unbounded, only audit/rollback/high-risk.

Current production code does not yet contain `progressive_replay.py`. Phase
18F already proves cursor-incremental runtime-gap replay for autonomy growth,
but L11 is the broader MAGMA memory/replay contract that unlocks compact cards,
delta chains, predictive prefetch, and later cold-tier storage work.

## Decision

The binding contract lives at:

```text
docs/eig2/contracts/progressive_replay_l0_l4.json
```

and validates against:

```text
.orchestrator/contracts/eig2_progressive_replay_contract.schema.json
```

Runtime implementations must preserve these invariants:

1. Raw MAGMA is the only source of truth.
2. Boot may load only L0 and bounded L1; L2, L3, and L4 are forbidden at boot.
3. Boot complexity is O(K), where K is the configured top-card count or
   equivalent bounded selector, not O(total MAGMA events).
4. Missing, stale, or malformed cards fall back to raw replay.
5. Hash mismatch fails closed to raw replay and verifies source hashes.
6. L4 is unbounded but only for audit, rollback, or high-risk paths.
7. Any derived writer needed for cards or secondary indices must satisfy
   ADR-011, ADR-014, and ADR-015.

## Alternatives Considered

1. Implement runtime first, document later. Rejected: replay strata are
   cross-cutting and need a stable budget/fallback contract before code lands.
2. Reuse Phase 18F incremental gap replay as the L11 contract. Rejected:
   Phase 18F proves one autonomy-growth replay lane, not the general MAGMA
   boot/hydration/forensic split.
3. Allow L4 at boot behind a profile flag. Rejected: this reintroduces the
   O(total events) boot behavior L11 is designed to remove.

## Consequences

- Future `progressive_replay.py` work has a machine-readable target.
- Compact-card and secondary-index work cannot become authoritative by accident.
- L12-L20 can depend on stable level names and budgets.
- A runtime PR that changes token budgets must update config and contract
  together or fail contract tests.

## Related Tests

- `tests/contracts/test_eig2_progressive_replay_contract.py`
- `tests/contracts/test_eig2_m0_contracts.py`

## Sign-off

- Author (Codex): signed 2026-05-12.
- Peer reviewer (Claude): requested 2026-05-12.
