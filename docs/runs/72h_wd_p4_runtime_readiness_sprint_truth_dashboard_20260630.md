# WD P4 Sprint Truth Dashboard - 2026-06-29T22:40Z

Sprint task: `codex-lead-1/wd-p4-runtime-readiness-sprint-20260629`.

Base for this dashboard PR: `origin/main` at
`56119e261d7b9343edbf780d105e1675c70506ad`.

This is a truth snapshot, not activation. Runtime activation, production
activation, scheduler enqueue, runtime mutation, rollback execution, bridge
append authority, and merge authority remain false in the artifact.

## Readiness

- Starting WD readiness: `42%`.
- Current counted WD readiness: `47%`.
- Target: `52%`; stretch: `55%`.
- Counted readiness comes only from merged or already-existing completed
  artifacts: seed #1 (+2), seed #2 (+2), and seed #3 (+1).
- Open PRs and CI-pending work are recorded as progress evidence but do not
  raise readiness until merged or explicitly superseded by stronger merged
  evidence.

## Seed Truth

| Seed | State | PR | Exact Head | CI | Gate / Blocker |
| ---: | --- | --- | --- | --- | --- |
| 1 | `merged` | #1434 | `d9acc02a399fea44c4ffc368ba2e8983e29f471f` | green | RCO wake/liveness preflight merged; counts for readiness |
| 2 | `merged` | #1436 | `89b4d4472da92778ed77d992e2627a05df4b5410` | green | merged to main as `56119e261d7b9343edbf780d105e1675c70506ad`; counts for readiness |
| 3 | `complete_existing` | n/a | n/a | targeted local green | rollback eligibility verifier exists; `tests/tools/test_auto_rollback_eligibility.py` passed |
| 4 | `consensus_pending` | #1437 | `4ebabd4efdba2d383f2ae2ad71156d1acd9e7ad2` | green | P4 adversarial corpus open; waiting non-author exact-head review/consensus |
| 5 | `planned` | n/a | n/a | not started | first re-derivable standing-sign `(b)` receipt not yet proven |
| 6 | `consensus_pending` | #1435 | `d24a7c023dec9ade99891e021e2175806489ea29` | green | strict digest fix verified locally; lead `build_consensus_pass` posted; waiting RCO refresh/gate completion |
| 7 | `pr_open` | this PR | pending | pending | this dashboard generator and snapshot |

## Local Evidence Recorded

- #1434 merged after green CI; no redo required.
- #1436 local evidence: `tests/tools/test_standing_sign_receipt_replay_canary.py`
  passed, CI is green, RCO1 and RCO2 pass at exact head, and the PR merged to
  main as `56119e261d7b9343edbf780d105e1675c70506ad`.
- Seed #3 local evidence:
  `python -m pytest tests\tools\test_auto_rollback_eligibility.py -q` passed
  with `17 passed`.
- #1437 local evidence: P4 corpus CLI reported `ok=true`, `36` cases, and
  `36` blocked cases; `tests/tools/test_p4_adversarial_corpus.py` passed; CI
  is now green.
  The full local suite was attempted because the selector returned full suite
  for the JSON corpus file, but it timed out after 20 minutes; CI remains the
  authoritative full-suite gate.
- #1435 local evidence at `d24a7c023dec9ade99891e021e2175806489ea29`:
  `tests/tools/test_hex_runtime_readiness_trace.py` passed with `9 passed`;
  related hex readiness/admission/rollup tests passed with `25 passed`;
  fake digest `sha256:` + `z` * 64 now fails both canonical well-formedness and
  single-shared-digest checks; CI is now green and lead `build_consensus_pass`
  is posted at the exact head.

## Residual Risk

- The sprint finish line is not complete: seed #4, #5, #6, and #7 are not
  merged/completed yet.
- The 52% target is not met in this snapshot.
- The first re-derivable standing-sign `(b)` proof remains the largest open
  milestone.
- #1435 and #1437 must still complete exact-head review/consensus after any
  head changes before they can count as merged readiness.

## Authority Boundary

- read-only report: `true`
- bridge append allowed by artifact: `false`
- queue write allowed by artifact: `false`
- scheduler enqueue allowed: `false`
- scheduler tick allowed: `false`
- runtime activation allowed: `false`
- runtime mutation authority: `false`
- production activation allowed: `false`
- rollback execution allowed: `false`
- merge allowed by artifact: `false`
- network required by artifact: `false`
