# CLAUDE.md — operator rules for Claude Code in this repo

Claude Code agents MUST follow these rules. They exist because on
2026-04-11 the `U:\project2` RAM-disk working tree disappeared together
with a full day of Phase 7 hologram/news/wiring work, commit `3babb93`
(`fix(hologram,feeds): HOLO-001 + NEWS-001/002/003 + WIRE-001`). The
rules below are written so that failure mode can never recur.

## Golden rules

1. **The only source of truth is the persistent C-drive repo.**
   Work exclusively in `C:\Python\project2`. Never develop in `U:\`,
   `R:\`, any RAM-disk, `%TEMP%`, or a zip-extraction folder.

2. **GitHub is the primary history. Backups are secondary.**
   Zip backups are for disaster recovery only. They must not be used
   as the canonical repo. If you are ever asked to work from a zip,
   clone GitHub into a persistent C-drive folder first, then overlay
   runtime data (DBs, chroma, models, logs) on top of the clone.

3. **Never `git init` on a restored backup snapshot.**
   A fresh `git init` erases every commit that ever pointed at that
   file tree — that is what caused the 2026-04-11 loss. The correct
   recovery is always:
   ```
   git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git C:\Python\project2_new
   # overlay current working data onto the clone (robocopy, excluding .git and any junctions)
   # commit + push the delta from the real HEAD
   ```
   See `docs/RECOVERY_POLICY.md` for the exact recipe.

4. **Every green checkpoint MUST be committed AND pushed.**
   "Green" = tests pass + smoke pass. Do not leave green work sitting
   in a local branch. Use `tools/savepoint.ps1` to enforce this:
   ```
   .\tools\savepoint.ps1 -Message "fix(...): ..." -TestPath "tests/test_foo.py"
   ```
   The script refuses to run off the C: drive, refuses to run from a
   RAM-disk, runs the tests you pass, commits, and pushes in one step.

5. **If you reconstruct work from reports, say so.**
   When the original source is lost and you reconstruct it from
   release-final reports (e.g. `C:\WaggleDance_ReleaseFinalRun\...\reports\`),
   put that explicitly in the commit message, include the report paths
   and the known-good RC commit SHA you are targeting.

## What this file does NOT override

- `AGENTS.md` task rules still apply.
- `.gitignore` still applies.
- The project's existing build, test, and release processes still apply.

## When in doubt

- Stop.
- Verify you are on the C: drive.
- Verify `git remote -v` points at the real GitHub repo.
- Verify `git status` is clean or that your in-progress work is staged.
- Then proceed.
