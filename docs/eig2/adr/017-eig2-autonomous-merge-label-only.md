# ADR-017 — `.eig2.autonomous_merge` is a PR label signal, not merge authority

Status: proposed
Author: Claude (Reality Check Owner, EIG2 Part 12.7)
Peer reviewer: Codex (signature pending)
Date: 2026-05-11
R-rule: R17 (Claude addition, agreed in bridge thread `claude-eig2-coldrehearsal-2026-05-11`)

Companion ADR: 016 (atomic-flip via STAGE2_CUTOVER_RFC) — R17 elaborates the residual semantic for `.eig2.autonomous_merge` once R16 strips its authority.

## Context

EIG2 Part 28.4 names a file `.eig2.autonomous_merge` at repo root, with the convention that startup-snapshotted content of exactly `YES_I_UNDERSTAND_THE_RISK` grants the EIG2 session authority to fast-forward merge to `main` and tag without an alpha suffix.

ADR 016 (R16) removed merge authority from this file: production atomic flips must go through STAGE2_CUTOVER_RFC.md; CLAUDE.md rule 10 stays load-bearing.

That leaves a question: does the file mean anything at all, or is the EIG2 mechanism dead?

The operator's intent in creating the file may be real (they want to signal "I've thought about the risks, proceed with stronger autonomy"). Discarding the file entirely would lose that intent. Repurposing the file as an informational signal preserves the intent without bypassing the gate.

## Decision

The `.eig2.autonomous_merge` file at repo root is a **signal of operator intent**, with the following bounded, documented effect:

1. **Startup snapshot rule (preserved from EIG2 Part 28.4):**
   - The EIG2 session reads `.eig2.autonomous_merge` exactly once at startup.
   - Writes `.orchestrator/autonomous_merge_snapshot.json` with `{exists_at_start, content_hash, enabled, read_at, must_ignore_later_changes: true}`.
   - Later changes to the file are logged (`autonomous_merge_flag_tamper_alarm`) but never grant authority.

2. **Effect when snapshot `enabled == true`:**
   - The EIG2 final report (`docs/eig2/final_autonomous_report.json`) includes `auto_merge_intent: assisted` (boolean true) and a documentation pointer to this ADR.
   - Every PR opened by the EIG2 session gains the GitHub label `auto-merge:assisted`.
   - The PR description includes a banner: "This PR has the operator's `auto-merge:assisted` intent. Reviewer authority unchanged; merge proceeds via normal review."

3. **Effect when snapshot `enabled == false` (or file absent):**
   - PRs receive no special label.
   - Final report `auto_merge_intent: none`.
   - Normal CLAUDE.md rule 9 autonomous-merge guardrails apply for docs/config PRs (head SHA match, CI green, mergeable, no rule violated).

4. **What `auto-merge:assisted` does NOT do:**
   - Does NOT trigger automatic merge.
   - Does NOT change required CI checks.
   - Does NOT lower the reviewer count requirement.
   - Does NOT bypass branch protection.
   - Does NOT grant authority for storage migrations / runtime topology changes / LLM chain rewiring (those go via ADR 016 / STAGE2 RFC).

5. **What `auto-merge:assisted` DOES do:**
   - Tells the human reviewer that the operator pre-acknowledged this work-stream's risk profile.
   - Surfaces in dashboard queries / search filters.
   - Provides an audit-trail link (PR description → ADR-017) for why the label is present.

## Alternatives considered

1. **Delete `.eig2.autonomous_merge` entirely.** Rejected: discards operator intent. The file is operator-controllable (per EIG2 Part 29); removing the language tells the operator we ignored their signal.
2. **Use the file as actual merge authority within a bounded scope (e.g., docs-only PRs).** Rejected: even bounded merge authority is structurally weaker than CLAUDE.md rule 9 (which already governs autonomous merge with explicit guardrails). Adding a second weaker gate is anti-helpful.
3. **Use the file to lower required-reviewer count.** Rejected: violates CLAUDE.md rule 9 spirit. Approval count is part of the guardrail set.

## Consequences

- EIG2 prompt language survives intact; just the *effect* is reinterpreted.
- Operator gets a clear, audit-trail-bound signaling mechanism distinct from STAGE2 RFC's pre-execution gate.
- The label can be queried (`gh pr list --label auto-merge:assisted`) for review-prioritization.
- Tamper alarm `autonomous_merge_flag_tamper_alarm` remains meaningful: mid-run changes are noise to the gate but evidence-worthy.

## Safety impact

Positive. The flag now has a defined, bounded effect. Operator never "thinks they granted merge authority by creating the file" — the label semantics are clear.

## Performance impact

Zero.

## MAGMA invariant impact

None.

## Audit / regression class

Maps to `INVARIANT_BREAK` if violated: any EIG2 session that grants itself merge authority based on `.eig2.autonomous_merge` content (rather than applying the label-only effect) is auto-classified by `bridge_classify.py` (PR2).

## Reviewed by other agent

Pending. Codex peer-review required. M6 signoff modes apply.

## Related tests

- (planned, PR2) `tests/orchestrator/test_eig2_autonomous_merge_label_only.py` — verifies the snapshot rules + label application + absence of merge authority.
- (planned, PR3) `tests/chaos/test_autonomous_merge_created_mid_execution_denied.py` (mentioned in EIG2 §28.4) — preserved as-is, augmented to verify the label-only semantics under this ADR.
- (planned, PR3) `tests/chaos/test_autonomous_merge_snapshot_only.py` — same, under this ADR.

## Provenance

Generalized from R17 binding-rule discussion in bridge thread `claude-eig2-coldrehearsal-2026-05-11` ts `2026-05-11T17:22:51.156126Z`. Direct evidence: ADR 016 stripping merge authority; need to define residual semantic for the file. Pattern: "preserve operator intent + tighten effect to a non-authority signal."

## Date

2026-05-11

## Sign-off

- Author (Claude): signed.
- Peer reviewer (Codex): pending.
