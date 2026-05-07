# P5 — dynamic epoch controller exercise

Per scenario list in the prompt, drove `Get-WaggleEpochCycleDecision`
through 7 representative cases. All match expected decisions and
branch letters.

| # | Scenario | Decision | Branch |
|---|----------|----------|--------|
| 1 | clean 2-iteration epoch (below target) | `continue` | M default |
| 2 | normal 3-iteration epoch (target reached + clean) | `trigger` | L (target_reached_and_clean) |
| 3 | medium issue at status=verification_pending | `continue_for_verification` | H |
| 4 | high issue, status=still_failing, 1 repair attempt | `continue_for_repair` | I |
| 5 | medium issue with `verified_by` previous iter (resurrection) | `trigger` | D (same_issue_resurrection_escalation) |
| 6 | 2 consecutive no-work iterations | `strategic_external_review` | C |
| 7 | 6-iteration epoch + open repair (hard ceiling) | `trigger` | J (hard_ceiling_reached) |

The remaining branch states (A `needs_manual_action` from the
classified_manual / pause flag, B `halt`, E repair-attempt cap,
F same-test-twice, severity-cap G, EG-* legacy early-triggers,
K below-min, M default, N legacy fallback) are exhaustively
covered by `Test-EpochCycleTrigger.ps1` (58/58 in the gate
suite).

## Decision audit record

Every decision in this exercise carried the audit fields
required by REL-013:
`decision_priority_branch`, `iterations_count`, `open_regressions_count`,
`max_regression_score`, `issues_in_repair`,
`issues_pending_verification`, `remaining_iterations_cap_for_this_epoch`,
`decided_at_utc`.

## Outcome

P5 PASS: dynamic epoch controller behaves correctly across the
7 representative scenarios.
