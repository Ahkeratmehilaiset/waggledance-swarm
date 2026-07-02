<!-- SPDX-License-Identifier: BUSL-1.1 -->
# P3 — Content-Identical Rebase Carry-Forward (V1)

**Status:** DRAFT spec (design-first). RFC item **P3 — "Cut churn:
content-identical-rebase carry-forward"** (*fable / rco-1 P3 / rco-2 P5*).
Producer-authored (`fable-5`); dual-RCO review; gate-policy → operator-sign.
**Changes no runtime behavior**. Doc-code truth corrected 2026-07-02: the
content-identical-base-rebase carve-out is a specified target, but it is **not
implemented in the current merge gate**. Today `verify_bridge_consensus`,
`check_rco_pass_present`, and the merge gate require fresh approvals at every new
head. P3 is the proposed mechanism that would make carry-forward real after its
predicate, adversarial tests, and gate wiring land as reviewed (a)-class code.
Lower priority than the charter-denylist (operator top-priority) and the P4
substrate; queued behind them.

## 0. One-paragraph summary

After every merge to `main`, open PRs' base refs go stale. Re-establishing them
normally means a rebase, and **head-exact binding invalidates all prior
approvals** (the correct default — a content-changing re-push must re-consensus).
But when the rebase is **content-identical** — the PR's diff against the new base
is **byte-identical** to its diff against the old base (no conflict edit, no
content change) — the *reviewed content is unchanged*, so full re-consensus is pure
toil. P3 would let such a rebase **carry the existing build + RCO approvals
forward** to the new head, **re-running CI only** (to catch semantic skew from the
advanced base). This is the specified Rule-9a carve-out, made mechanical and
fail-closed, but it is not active until gate code implements it. It attacks the
documented stale-base bottleneck (repeatedly ~17 of 19 open PRs stale after a
merge) without loosening any gate.

## 1. The carve-out it would mechanize (CLAUDE.md Rule 9a specified intent)

> **content-identical base rebase:** a pure rebase onto current `origin/main` with
> **no content change** (the PR's diff against the new base is byte-identical to its
> diff against the prior base — mechanically verified, no conflict-edit) **carries
> the consensus approvals forward** to the new head, because the reviewed content is
> unchanged. CI **must still be re-run green** against the new head before merge (to
> catch semantic skew from the advanced base). The carry-forward applies to
> content-review approvals only, never to CI; any content difference (conflict
> resolution, edit) forfeits it and forces full re-consensus.

Current implementation caveat: the text above is the specified target, not the
runtime gate. Until P3 lands in gate code, any re-push/rebase strands prior
approvals and requires re-consensus at the new exact head. P3 adds **nothing** to
the intended authority boundary; it only makes the "mechanically verified" test
explicit and automatable for a future implementation.

## 2. The content-identical test (mechanical, fail-closed)

A rebase `old_head (base=B_old)` → `new_head (base=B_new=origin/main tip)` is
**content-identical** iff ALL hold:

1. **Same diff by patch-id.** `git patch-id --stable` of the PR's full diff is
   identical before and after: `patch-id(diff(B_old…old_head)) ==
   patch-id(diff(B_new…new_head))`. (Patch-id is whitespace/line-number-stable and
   ignores the base context, so it captures "same change" precisely.)
2. **No conflict resolution / no edit.** The rebase applied cleanly with zero
   conflict markers and zero manual edits — equivalently, the patch-id equality in
   (1) holds AND the changed-file set is identical.
3. **Pure base advance.** `B_new` is the current `origin/main` tip and
   `B_old` is an ancestor of `B_new` (a forward rebase, not a cross-graft).
4. **Author identity unchanged.** Same PR author; the rebase introduces no new
   author/committer of content (independence preserved).

Any failure of 1–4 → **NOT content-identical** → carry-forward forfeited → full
re-consensus at the new head (the normal head-exact-binding default). Unparseable
diff / ambiguous patch-id / tooling error → **fail-closed** (forfeit; re-consensus).

## 3. What carries forward — and what does NOT

**Would carry forward after P3 is implemented** (only when §2 holds), re-anchored
to `new_head`:
- `build_consensus_pass` (lead + tools) — the reviewed content is unchanged.
- recognized-RCO `RCO_PASS` — same.
- The carry-forward is recorded with the **proof**: old_head, new_head, the shared
  patch-id, and the §2 checks, in a MAGMA carry-forward receipt (re-derivable).

**Does NOT carry forward (always re-required at the new head):**
- **CI** — MUST re-run green at `new_head` (semantic skew from the advanced base is
  exactly what carry-forward cannot see; this is the non-negotiable backstop).
- **operator-sign** — an off-allowlist / operator-sign-by-content PR still needs a
  fresh operator action at the new head; P3 NEVER carries an operator signature
  forward (it is content-review carry-forward only).
- **A standing RCO veto / changes_requested** — a block carries forward as a block
  (carry-forward never clears a veto; it only preserves *passes* on unchanged
  content). An unresolved block at old_head remains a block at new_head.

## 4. Fail-closed / non-loosening invariants

- ANY content difference (conflict edit, whitespace-significant change, file-set
  change) → forfeit → full re-consensus. Carry-forward is the *exception*, default
  is re-consensus.
- CI always re-runs (never carried). Carry-forward + red CI = blocked.
- operator-sign never carried. RCO veto never cleared by carry-forward.
- Patch-id ambiguity / tooling failure → fail-closed (forfeit).
- P3 changes only **whether unchanged-content approvals must be re-typed**; it
  grants no new merge authority, loosens no gate, and is bounded to the operator-
  ratified Rule-9a carve-out.

## 5. Interaction with the other RFC pieces

- **vs head-exact binding (P2/D5, #1387 incident):** head-exact binding stays the
  default; P3 is the *one* mechanically-proven exception. A carry-forward must
  produce a receipt the gate can re-derive (same discipline as every other
  approval), so it can never be a free-text claim.
- **vs auto-rebase (tools P2 proposal):** P3 is the *carry-forward decision*; an
  auto-rebase tool may USE it only after gate wiring lands (rebase a
  stale-but-content-identical PR, carry approvals, re-run CI), but the two are
  separable — P3 is the predicate, the auto-rebase is the actuator
  (off-allowlist ops).
- **vs P4b canary:** unrelated; P3 is pre-merge churn reduction, P4b is post-merge.

## 6. Rollout & ownership

1. **This spec** (fable-5; dual-RCO; operator-sign — gate-policy). Per the operator
   top-priority, lands AFTER the charter-denylist (which will make this spec doc
   itself off-allowlist by construction).
2. **`tools/check_content_identical_rebase.py` + tests** — a DORMANT pure predicate
   (`is_content_identical(old_diff, new_diff, old_base, new_base, author) -> bool +
   reason`, patch-id based), consulted by nothing. Off-allowlist → operator-sign.
3. **Gate/auto-rebase wiring** — lead/tools wire the predicate into the consensus
   verifier / an auto-rebase actuator; off-allowlist → operator-sign; never
   activates until signed.

**Non-goals:** not a merge actuator; not a way to skip CI; not a way to carry an
operator signature or clear a veto. Strictly: re-anchor *unchanged-content* passes
to a content-identical new head, CI re-run mandatory.
