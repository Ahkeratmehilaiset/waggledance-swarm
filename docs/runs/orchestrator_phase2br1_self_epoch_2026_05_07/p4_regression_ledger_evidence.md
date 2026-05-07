# P4 — regression ledger exercise

## State machine walk

Starting from a fresh empty ledger, walked the full state chain
on a critical (score 90) entry:

```
detected -> open
open -> classified_local_repair
-> repair_prompt_generated
-> repair_iteration_in_progress
-> fix_attempted
-> verification_pending
-> verified
-> reopened
```

All 7 transitions accepted. Status updates appended history events
correctly.

## Illegal transitions rejected

| From | To | Result |
|------|----|--------|
| `classified_external` | `verification_pending` | **THROWS** ("illegal status transition: classified_external -> verification_pending") ✓ |
| `fixed` | `still_failing` | **THROWS** ("illegal status transition: fixed -> still_failing") ✓ |

`Update-WaggleRegressionEntry` enforces the transition table from
`Script:RLAllowedTransitions` and refuses to silently accept.

## Severity scoring 0–100

| score_categories | sum | severity |
|------------------|-----|----------|
| `doc_report_mismatch` | 5 | info ✓ |
| `no_work_stall + source_supplement_sparse` | 20 | low ✓ |
| `hardening_gate_failure` | 40 | medium ✓ |
| `hardening_gate_failure + ci_failure` | 70 | high ✓ |
| `hardening_gate_failure + ci_failure + runtime_crash` | 90 | critical ✓ |
| all 9 categories combined | (sum > 100) → **capped at 100** ✓ |

Severity band derivation (info / low / medium / high / critical
on 0-19 / 20-39 / 40-59 / 60-79 / 80-100) matches design.

## High/critical entries affect epoch controller

Verified via `Get-WaggleEpochCycleDecision` with the new bounds
config (min=2, target=3, max=6):

| Open issue | Score | At iter | Remaining cap | Expected |
|------------|-------|---------|---------------|----------|
| critical | 85 | 2 of 6 | **3** | current+1 = 3 ✓ |
| high | 70 | 2 of 6 | **4** | current+2 = 4 ✓ |
| medium | 50 | 2 of 6 | **6** | unchanged = 6 ✓ |

## Outcome

P4 PASS: regression ledger lib + state machine + scoring rubric
+ severity bands + epoch-controller integration all behave as
designed.
