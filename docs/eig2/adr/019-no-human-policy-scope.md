# ADR-019 — No-human policy applies to implementation decisions, not repo safety policy

Status: Accepted for EIG2-M0 (Codex peer-review signed 2026-05-11)
Author: Claude (Reality Check Owner, EIG2 Part 12.7)
Peer reviewer: Codex (signed 2026-05-11)
Date: 2026-05-11
R-rule: R19 (Claude addition, agreed in bridge thread `claude-eig2-coldrehearsal-2026-05-11`; Codex flagged the scope concern in his pass-1 simulation)

## Context

EIG2 v1.1 has three sections that, read maximally, suggest the agents should never wait for human input or human confirmation:

- **Part 1.3** "No-human-interaction policy": "No implementation-time step may require a human question, human approval, human confirmation, or human review to continue."
- **Part 11.1** "No human prompts during implementation": "Agents must not ask a human for confirmation, acceptance, architecture approval..."
- **Part 33** "No-human prompt lint": scans for forbidden phrases like "ask user", "manual approval required", etc.

The WaggleDance repository's `CLAUDE.md` has separate operational rules that are explicitly human-gated:

- **Rule 6** PR-only — no direct push to main, even for trivial / typo / state-file changes.
- **Rule 8** Strongest-model default — operator decides model lane choices.
- **Rule 9** Autonomous-merge guardrails — merge requires (a) head SHA match, (b) CI green, (c) mergeable clean, (d) no rule violation.
- **Rule 10** Atomic-flip discipline — HUMAN_APPROVAL.yaml collected at execution time, operator-only.
- **Rule 11** Trivial-rationalization warning — "it's just docs / one line" is not a license to bypass any gate.

If "no human interaction" is read literally, it overrides CLAUDE.md rules 6 / 9 / 10. That would invert the safety model: the operator's repo-level constraints become bypassable by the agent at runtime.

Codex flagged this in his pass-1 simulation (bridge `eig2-cold-practice-simulation-2026-05-11` ts `2026-05-11T17:17:10.4360357Z`): "prompt no-human policy conflicts with AGENTS/operator safety rules for destructive/legal/security decisions; resolve by scoping no-human to implementation decisions, not repository safety policy."

## Decision

The EIG2 no-human-interaction policy (Parts 1.3, 11.1, 33) applies to **implementation decisions only**. It does NOT override repository safety policy.

**Implementation decisions** (no-human applies):
- Architecture choices (which adapter shape to use, what topology providers to implement).
- Schema design (tunnel edge shape, compact card fields, replay budgets).
- Tunnel scoring weights and Hebbian constants (Part 6).
- Regression triage classifications (bridge_classify.py output handling).
- Test fixture decisions.
- Code refactoring choices.
- ADR authorship and peer-review routing.
- Bridge protocol convention decisions (e.g., ADR 020 — type field non-gating).

For these, the agents reach consensus via M6 trust-filter (bridge push-back rounds) and proceed without operator prompts.

**Repository safety policy** (NOT overridden by EIG2 no-human policy):
- CLAUDE.md rule 6 — PR-only. EIG2 sessions still open PRs for every change. No direct push to main.
- CLAUDE.md rule 8 — Strongest-model default. Operator owns the model choice; agents do not silently downgrade.
- CLAUDE.md rule 9 — Autonomous-merge guardrails. Merge requires all four conditions (head SHA match, CI green, mergeable clean, no rule violation). EIG2 does not relax these.
- CLAUDE.md rule 10 — Atomic-flip discipline via STAGE2_CUTOVER_RFC.md + HUMAN_APPROVAL.yaml. Operator-only at execution time. EIG2 does not author or collect this approval (see ADR 016).
- CLAUDE.md rule 11 — Trivial-rationalization warning. EIG2 sessions do not use "it's just docs" to bypass any gate.

**The Autonomous Safety Fence (EIG2 Part 11.2) operates within these bounds.** When EIG2 v1.1 says "Whenever any earlier or external instruction says 'human review required', execute the safety fence instead" — that text applies to implementation-decision items that *would have* required runtime operator prompts in pre-EIG2 sessions. It does NOT recompose the repository's permanent operator-only gates (rules 9, 10, 11).

## Alternatives considered

1. **Literal reading: EIG2 fully overrides CLAUDE.md.** Rejected: inverts the safety model. Operator-level constraints become agent-runtime-bypassable. Violates the principle that meta-policy outranks task-policy.
2. **EIG2 partial override: agents may bypass rule 9 (autonomous-merge guardrails).** Rejected: rule 9's four conditions are not human prompts — they are mechanical gates. There is no human-interaction cost to satisfying them. Bypass gain is negative.
3. **EIG2 honors CLAUDE.md but adds extra friction at runtime.** Rejected: that defeats the no-human-interaction goal. The fix is scoping, not adding friction.

## Consequences

- EIG2 sessions operate fully autonomously inside the implementation-decision scope.
- EIG2 sessions still go through PR review for code landing — operator gate at merge time, not at runtime.
- EIG2 sessions never trigger CLAUDE.md rule 10 work autonomously; if Stage-2 cutover is needed, the EIG2 session writes a proposal in `docs/eig2/proposals/` and continues with the feature disabled (per Part 11.2 Autonomous Safety Fence).
- The no-human-prompt lint (Part 33) is unchanged — it scans implementation-control text. CLAUDE.md text is not implementation-control; it is repo policy.

## Safety impact

Strongly positive. Resolves the read-conflict between EIG2 no-human policy and CLAUDE.md rules 6 / 9 / 10 / 11. Operator's repo-level constraints remain load-bearing.

## Performance impact

Zero direct cost. Indirect benefit: no time wasted on confused-agent prompts about whether to ask the operator.

## MAGMA invariant impact

None directly. Protects: prevents EIG2 from claiming MAGMA-schema-migration authority via no-human override.

## Audit / regression class

Maps to `INVARIANT_BREAK` if violated: any EIG2 session action that bypasses a CLAUDE.md rule on the grounds of "Part 1.3 says no human interaction" is auto-rejected by `bridge_classify.py` (PR2 regex for the rationalization pattern).

## Reviewed by other agent

Codex reviewed and endorses. The scope boundary matches the operator's current
directive: do not ask during implementation, but preserve repository safety
rules for destructive, legal, credential, and security-sensitive operations.
This is the correct reconciliation between EIG2 Parts 1/11/33 and AGENTS.md.

## Related tests

- (planned, M1+) `tests/orchestrator/test_no_human_policy_scope.py` — verifies the classifier rejects rule-9-bypass rationalizations.
- (planned, PR3) `tests/contracts/test_eig2_repo_safety_unchanged.py` — contract test that CLAUDE.md rule line counts and key phrase markers are unchanged by any EIG2 PR.

## Provenance

Generalized from R19 binding-rule discussion in bridge thread `claude-eig2-coldrehearsal-2026-05-11` ts `2026-05-11T17:22:51.156126Z`. Codex flagged the underlying scope concern earlier in `eig2-cold-practice-simulation-2026-05-11` ts `2026-05-11T17:17:10.4360357Z`. Direct evidence: live read-conflict between EIG2 Parts 1.3 / 11.1 / 33 and CLAUDE.md rules 6 / 9 / 10 / 11.

## Date

2026-05-11

## Sign-off

- Author (Claude): signed.
- Peer reviewer (Codex): signed 2026-05-11. (Substance pre-endorsed in pass-1 simulation; formal review completed in PR3.)
