# Phase 2A-1 — git hygiene (P1)

## Decision

Local-only excludes via `.git/info/exclude` (NOT repo `.gitignore`).
This satisfies global rule 14: "Prefer `.git/info/exclude` over repository
`.gitignore` for local orchestrator outputs/config/state."

## Worktree note

`C:\Python\project2-master\.git` is a worktree pointer file:

```
gitdir: C:/Python/project2/.git/worktrees/project2-master
```

The shared `info/exclude` lives at `C:\Python\project2\.git\info\exclude`
and is honoured by this worktree (verified via `git check-ignore -v`).

## Exact entries appended

Appended to the existing `C:\Python\project2\.git\info\exclude` (after the
prior `x.txt` / `phase1_prompt.md` block):

```
# WaggleDance orchestrator local-only outputs (Phase 2A-1 hardening).
# Kept in .git/info/exclude (NOT repo .gitignore) so the public ignore set
# stays clean and these patterns do not bleed into other clones / worktrees.
/iterations/
/state/
/orchestrator.config.json
/orchestrator.config.*.bak.json
/prompts/smoke.md
/prompts/phase2a1_hardening.md
/hello-from-orchestrator.txt
/.claude/
/docs/runs/orchestrator_phase2a1_hardening_2026_05_06/
*.pid
*.tmp
*.log.local
```

## Verification

```
> git check-ignore -v iterations/sample/state.json
.git/info/exclude:18:/iterations/  iterations/sample/state.json
> git check-ignore -v state/current.json
.git/info/exclude:19:/state/  state/current.json
> git check-ignore -v orchestrator.config.json
.git/info/exclude:20:/orchestrator.config.json  orchestrator.config.json
> git check-ignore -v orchestrator.config.phase1_6.bak.json
.git/info/exclude:21:/orchestrator.config.*.bak.json  orchestrator.config.phase1_6.bak.json
> git check-ignore -v prompts/smoke.md
.git/info/exclude:22:/prompts/smoke.md  prompts/smoke.md
> git check-ignore -v hello-from-orchestrator.txt
.git/info/exclude:24:/hello-from-orchestrator.txt  hello-from-orchestrator.txt
> git check-ignore -v .claude/foo
.git/info/exclude:25:/.claude/  .claude/foo
> git check-ignore -v iterations/2026-05-06_15-49-51/state.json
.git/info/exclude:18:/iterations/  iterations/2026-05-06_15-49-51/state.json
> git check-ignore -v docs/runs/orchestrator_phase2a1_hardening_2026_05_06/baseline_inventory.md
.git/info/exclude:26:/docs/runs/orchestrator_phase2a1_hardening_2026_05_06/  ...
```

All target paths are now ignored.

## No tokens printed

No `gh auth token`, `gh auth git-credential get`, credential-helper
diagnostics, or remote-URL printing was used during this phase. No tokens
appear anywhere in this report.

## Pre-existing condition NOT changed by this phase

`git check-ignore orchestrator/lib/Redactor.ps1` matches because the repo's
public `.gitignore` line 20 contains the standard Python pattern `lib/`,
which incidentally swallows `orchestrator/lib/`. The orchestrator source
tree is not currently tracked in this branch (`git ls-files orchestrator/`
returns 0 files).

This is not introduced by P1 — `.git/info/exclude` adds no `orchestrator/`
patterns. P1 deliberately does NOT touch the public `.gitignore` to avoid:
- breaking unrelated downstream tooling that already depends on `lib/` ignore;
- shipping a project-wide policy change in a hardening-only iteration.

A focused fix (e.g. add `!orchestrator/lib/` exception to the public
`.gitignore`) belongs to a separate iteration that is allowed to land
public ignore policy. PHASE2A2_HANDOFF will note this so the next phase can
choose to address or defer.

## Remaining git hygiene warnings

- Public `.gitignore lib/` shadows `orchestrator/lib/` (pre-existing,
  out of scope for P1).
- `orchestrator.config.json` is listed via `.git/info/exclude` so an
  operator who runs `git add orchestrator.config.json` explicitly will
  still be able to bypass the exclude (git allows `-f` overrides). This
  is the correct level: local-only, not a hard block.
- `*.tmp`/`*.pid`/`*.log.local` are catch-alls. They will not catch
  `orchestrator/lib/Lockfile.ps1` because that is `.ps1`, not `.tmp`.
