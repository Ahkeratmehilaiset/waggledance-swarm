# RCO Review Axes v1

**Status:** proposed (2026-07-01)
**Author:** fable-5
**Relationship to the gate:** COMPLEMENTARY, non-normative to verdict computation.
This spec is a review *methodology*. It does **not** modify the bridge-consensus
verdict gate (`verify_bridge_consensus`, `check_rco_pass_present`,
`check_bridge_changes_requested`, `idle_consensus_auto_merge`) or any executor,
and it does **not** live on the runtime verdict path. See
`docs/architecture/BRIDGE_CONSENSUS_APPROVAL_V1.md` for the authoritative gate.

## Purpose

The dual-RCO requirement (`{claude-rco-1, claude-rco-2}`, both mandatory for
best-possible consensus, veto-absolute per identity) is load-bearing for
**safety**. But two reviewers drawn from the **same model family** tend, absent a
methodology, toward **parallel** coverage — both attacking a change from the same
default angle. Parallel coverage has two failure modes:

1. **Wasted second reviewer** — the same properties get checked twice while whole
   classes of risk go unexamined.
2. **Correlated blind spots** — same-family reviewers can make the *same* mistake
   at the same time. This has happened: two recognized reviewers converged on the
   same shallow-clone "disjoint history" false alarm (PR #1439), and on a
   divergent-worktree gate false alarm — both a shared environmental blind spot.

This spec puts the two RCOs at an **angle** to each other: mostly-orthogonal
coverage so the same thing is not tested twice, plus a small deliberate **overlap**
on the single highest-risk change for cross-verification. It optimizes
**coverage**; it changes **nothing** about the gate's safety guarantees.

## Principle

- **Angle, not parallel.** The two RCOs cover different axes. Their findings
  *sum*; they do not overlap by default.
- **Deliberate overlap where it matters.** Both independently review the single
  highest-risk hunk of the diff — a cross-verification vote on the critical core.
- **Coverage is optimized here; safety is optimized by the gate.** The
  dual-mandatory + veto-absolute + author≠reviewer structure (unchanged) remains
  the safety floor. This methodology sits on top and never substitutes for it.

## The two axes

### Axis A — `claude-rco-1`: adversarial ("can I break it")
- Security posture: fail-open vs fail-closed, input perturbation (malformed,
  boundary, injection, wrong-type), SSRF / path-traversal / secret handling,
  authority/dormancy boundaries.
- **Must RUN** at least one adversarial / perturbation case standalone (e.g.
  feed the pure check a crafted input, flip each protected field) — not a
  structural read. Cite the command and the observed result.

### Axis B — `claude-rco-2`: regression ("does it break what exists")
- **Must RUN** the affected + existing regression surface: targeted tests, the
  registry/loader/consumer tests the change touches, and the truth-regression /
  phase-invariant suites. Check blast-radius / scope and API-contract
  compatibility for existing consumers.
- Cite the concrete run result (e.g. "`pytest tests/v3_13_0 -q` → 756 passed,
  1 skipped"), not a read-only judgement.

### Shared overlap — the highest-risk hunk
- Both RCOs independently review the single riskiest change in the diff (the one
  that, if wrong, does the most damage). This is the cross-check vote; it is the
  only place double-work is intended.

## Run-not-read mandate

Every `RCO_PASS` must cite **concrete run evidence on its axis** — commands plus
results — not a logic/structural read. This encodes a repeated lesson: reviews
passed on reading have missed bugs that *running* caught (the build lane, which
runs, has repeatedly been the actual catch). If an RCO cannot run its axis, it
**must not pass** (fail-closed) — silence and "looks correct" are not a pass.

## De-correlation of environment

Review against the **PR blob** or a **fresh canonical `origin/main` worktree** —
never a local, possibly-divergent working tree. The shallow-clone (#1439) and
divergent-worktree false alarms were shared environmental blind spots; verifying
against the canonical artifact breaks that correlation. Before asserting any gate
finding, confirm the verifier you ran matches `origin/main`
(`git diff --stat origin/main -- tools/<verifier>.py`); if it differs, run the
canonical version.

## Axis assignment and degraded modes

- Default assignment is by identity: `claude-rco-1` = Axis A, `claude-rco-2` =
  Axis B.
- **Author ≠ reviewer still governs.** If a recognized RCO authored the PR, only
  the *other* recognized RCO can satisfy the RCO slot; that RCO then covers
  **both** axes (degraded coverage, still safe) — fail toward *more* coverage,
  never less. A third recognized identity, if available, restores the split.
- Axis assignment is a coverage convention. It never changes *which* identities
  the gate recognizes, nor the requirement that **both** recognized RCOs pass at
  the exact head for best-possible consensus.

## Explicitly non-weakening (relationship to the gate)

This spec adds discipline; it removes nothing:

- The gate still requires **DUAL-RCO `RCO_PASS`@head** and treats a
  veto/finding from **either** recognized RCO as an absolute block (veto outranks
  pass). Unchanged.
- A finding on **either** axis blocks the merge. The axes decide *where each RCO
  looks first*, not *whether a problem blocks*.
- No verdict-computing file, executor, allowlist, or charter check is modified by
  this document. It is gate-adjacent methodology, off the runtime verdict path.

## Out of scope

- No change to the (a)/(b) standing-consensus-sign split, to Rule-10 / Stage-2
  cutover discipline, or to any operator-explicit authority.
- No change to CI required checks; CI remains the deterministic regression
  backstop and is not replaced by Axis B.
