# Continuity alert ledger rotation proposal

Status: Proposed, not approved or installed. Owner: codex-tools-1. Date: 2026-10-12.

Continuity alerts must remain deliverable after the active ledger fills without losing evidence or sending an uncertain alert twice. This proposal owns the open round-2 item “alert ledger rotates instead of stopping when full.” Independent ADR and RCO review must precede implementation and any maintenance of the installed ledger. Supervisor remains OFF; this proposal grants no runtime authority.

## Incident and prerequisite

The Tools thread ledger contains 256 entries, including 204 with reason `hold_possible`. Its final entry records the capacity notice at 2026-10-11 16:15:03Z. Subsequent new alerts return `unknown ledger_full`; a published capacity notice does not prove delivery of later alerts.

The 2026-10-12 08:13:47Z alert is reproducible from the saved checkpoint. The installed native adapter builds a `checkpoint_only` snapshot with empty control-event and HOLD arrays. There is no bridge control event behind this alert. Free-text matching instead finds `cancel` inside the CI identifier `stablecancelgroup`, and `signature` in the non-operative release-policy statement “signature unchanged.” The checkpoint declares `work_held=false` and `release_held=true`. Release restrictions remain binding but are not a pause of preparation.

Correct this classification before maintenance. Explicit pauses, cancellations, scoped HOLDs and unresolved control language must still fail closed. Do not create space merely to suppress a real HOLD.

## Proposed rotation contract

Use the existing per-thread ledger lock, not another collector or a second delivery owner. Rotation must refuse inconsistent agent/thread identity, unsafe paths, unresolved `sending` or uncertain entries, or a ledger that changed during preparation. Reconcile pending bridge writes using the existing canonical receipt mechanisms before proceeding.

Prepare an exclusive archive under the lane audit directory using the full ledger's exact bytes. Record byte length, SHA-256, schema, agent/thread, entry count and the predecessor epoch. Verify the archive by reading its bytes back while holding the existing lock. Preserve the original full ledger unchanged as well; never delete, truncate or rewrite its entries.

Create a new empty ledger at a new epoch-specific path, not over the original filename. A small, atomically published active-epoch pointer identifies the new ledger and the immutable predecessor archive. The pointer is the sole cutover commit point. The reviewed reader and writer must validate the entire predecessor chain and reject missing, corrupt or contradictory archives. Older installed readers remain fail-closed on the original full filename until an authorized generation change; no hot pointer substitution is allowed.

Deduplicate across all sealed epochs and the active ledger, preserving the existing key and uncertain-delivery semantics. A pointer reset must not replay any published alert, clear an uncertain intent or convert queue acceptance into completion. Repeated maintenance requests with the same proven epoch resume the existing transaction; they do not create another epoch.

The maintained ledger may rotate again under this same reviewed contract before reaching capacity. Archive retention is not a cleanup permission. A quota or storage bound refuses maintenance with a visible notice and preserves all epochs.

## Required RED tests and rollout evidence

Test byte-exact preservation; full-ledger rotation; already-published key deduplication across epochs; pending or uncertain write refusal; agent/thread mismatch; archive collision and corruption; unsafe path rejection; concurrent alert versus rotation; and crashes before archive verification, before pointer publication and after pointer publication. Each test must name the mutant it rejects.

A private fixture must prove that a new alert can be published after rotation, that an old key is not published again and that a real work HOLD remains stopped. Both eligible RCOs review this alert-delivery boundary and the ADR before authorized installation. Checkpoint and bridge evidence must name the exact generation, ledger and archive hashes, delivery receipts and observation interval. Until then, the installed ledger remains unchanged and this plan item stays open.
