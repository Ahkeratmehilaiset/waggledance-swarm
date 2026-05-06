# P13 -- Release decision

## Inputs to the decision

| Item | Status |
|---|---|
| baseline (P0) | green |
| git hygiene (P1) | green |
| Phase 2A-1 hardening gates re-run (P2) | green (99/99) |
| real-Claude smoke from clean session (P3) | green (iter `2026-05-06_19-45-54` COMPLETED) |
| design doc (P4) | green |
| review config + 3 role templates (P5) | green |
| review schema + adapter library (P6) | green (16/16 + 38/38) |
| `Invoke-WaggleReview.ps1` + tests (P7) | green (69/69) |
| hardening gate driver `Run-WaggleHardeningGates.ps1` + `Test-Phase2A2` (P8) | green (8 gates / 53 cases) |
| real architect / security / reliability review (P9) | green (3/3 COMPLETED, role-specific outputs) |
| usage doc + validation summary + Phase 2A-1 handoff append (P10) | green |
| final local validation -- repeat hardening + smoke + reviews (P11) | green |
| manifest self-check (P12) | green (19/19 promised files exist + parse + non-empty + 0 real secrets) |
| no token / secret printed | yes (verified by inspection + grep) |
| no browser automation | yes (no Playwright / Selenium / WebView code added) |
| no tag, no release | yes (the production path P14 will not create either) |
| no new pip / npm dependency | yes (no requirements / package.json change) |

## Decision

**Decision A: proceed to commit, push, PR, CI wait, squash merge.**

Per master prompt:

- PR-only landing.
- No `gh auth token` / `gh auth git-credential get`.
- Plain `git push -u origin orchestrator/phase2a2-claude-self-review`.
- `gh pr create` with body file.
- `gh pr checks --watch`.
- `gh pr merge --squash --match-head-commit <head_sha>`.
- No tag.
- No GitHub release.
- No prerelease tag.

## What does NOT happen

- Phase 2B is not auto-started. After merge, human review is required
  before the multi-LLM lane / auto-fix loop / Phase 2B scope.
- No release docs PR is generated unless P15's post-merge verification
  produces content that needs to land in main; if so, that landing is
  a separate docs-only PR with the same rules (no tag).
