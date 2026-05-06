# P1 git hygiene

## Branch

Created and checked out:

```
orchestrator/phase2a2-claude-self-review
```

Off `main` at `7210a7e Phase 16D — final stable gate closure: Docker and Bandit (#66)`.

## .gitignore change (committed)

The repo's public `.gitignore` had a broad `lib/` rule (line 20) shadowing
the orchestrator's PowerShell library at `orchestrator/lib/`. We added a
minimal explicit re-include block at the end of `.gitignore`:

```
!orchestrator/lib/
!orchestrator/lib/**
!orchestrator/lib/review/
!orchestrator/lib/review/**
```

We did NOT loosen the broad `lib/` rule itself (it still excludes Python
build `lib/` directories — by leaving the deny rule and adding a
`!`-allow rule below, only the orchestrator subtree is allowed back in).

## .git/info/exclude rewrite (NOT committed)

Phase 2A-1 had marked `prompts/smoke.md`, `prompts/phase2a1_hardening.md`,
and `docs/runs/orchestrator_phase2a1_hardening_2026_05_06/` as local-only.
Phase 2A-2 must commit those, so we rewrote `.git/info/exclude` to
remove those entries and add the new local-only paths the master prompt
listed:

```
/iterations/
/iterations_p5_test/
/iterations_review/
/state/
/state_p5_test/
/orchestrator.config.json
/orchestrator.config.*.json
/orchestrator.config.*.bak.json
/hello-from-orchestrator.txt
/.claude/
*.pid
*.tmp
*.local
*.log.local
```

The catch-all `/orchestrator.config.*.json` will also match the committed
`orchestrator.config.review.example.json`. P14 staging force-adds that
file with `git add -f` to override; everything else under that pattern
is local-only scratch.

`.git/info/exclude` itself is NOT a tracked file and is NOT staged.

## Verification

`git check-ignore -v` results (run after the changes):

| Path | Status | Rule |
|---|---|---|
| `orchestrator/lib/Redactor.ps1` | NOT ignored | `.gitignore !orchestrator/lib/**` |
| `orchestrator/lib/ArtifactValidator.ps1` | NOT ignored | `.gitignore !orchestrator/lib/**` |
| `orchestrator/lib/CompletionVerifier.ps1` | NOT ignored | `.gitignore !orchestrator/lib/**` |
| `orchestrator/Invoke-WaggleIteration.ps1` | NOT ignored | (no rule) |
| `prompts/smoke.md` | NOT ignored | (no rule) |
| `docs/runs/orchestrator_phase2a1_hardening_2026_05_06/final_report.md` | NOT ignored | (no rule) |
| `orchestrator.config.json` | IGNORED | `.git/info/exclude /orchestrator.config.json` |
| `iterations_p5_test/dummy` | IGNORED | `.git/info/exclude /iterations_p5_test/` |
| `state_p5_test/dummy` | IGNORED | `.git/info/exclude /state_p5_test/` |
| `orchestrator.config.p5_test.json` | IGNORED | `.git/info/exclude /orchestrator.config.*.json` |
| `hello-from-orchestrator.txt` | IGNORED | `.git/info/exclude /hello-from-orchestrator.txt` |
| `.claude/x` | IGNORED | `.git/info/exclude /.claude/` |
| `state/current.json` | IGNORED | `.git/info/exclude /state/` |
| `iterations/foo` | IGNORED | `.git/info/exclude /iterations/` |

`git status --short` shows the orchestrator and prompts trees as `??`
(untracked, available to stage) and the Phase 2A-1 docs run dir as `??`
(visible). `iterations_p5_test/`, `state_p5_test/`,
`orchestrator.config.p5_test.json`, `state/`, `iterations/`,
`orchestrator.config.json`, `hello-from-orchestrator.txt`, `.claude/`
do NOT appear in `git status` — confirming they will not be accidentally
staged.

## Done

P1 PASS.
