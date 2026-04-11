# RECOVERY_POLICY.md

This document exists because on 2026-04-11 a full day of post-v3.5.6
Phase 7 hologram/news/wiring work (commit `3babb93`) was irretrievably
lost when the `U:\project2` RAM-disk working tree disappeared. The
reports survived; the source code did not. The rules below are written
so that exact failure mode can never recur.

## Non-negotiable rules

1. **Persistent C-drive is the only source of truth.**
   Work happens in `C:\Python\project2`. Never develop in `U:\`, `R:\`,
   any RAM-disk, `%TEMP%`, or a zip-extraction folder. RAM-disks are
   volatile by design and they take uncommitted work with them on reboot
   or drive unmount.

2. **GitHub is the primary history. Backups are secondary.**
   Zip backups (`C:\WaggleDance_Backups\*.zip`) are disaster-recovery
   snapshots, not development checkpoints. They must NEVER be used as
   the canonical repo. A zip that has no `.git` directory cannot store
   history — using it as the working tree silently throws away every
   prior commit.

3. **Push every green checkpoint immediately.**
   "Green" means tests pass and the live smoke matrix passes. The
   moment a checkpoint is green, commit and push. Do not batch multiple
   greens into one push. Do not leave a green commit sitting on a
   single machine overnight.

4. **Never `git init` a backup snapshot.**
   `git init` creates a fresh history with no parents — it erases
   every commit that previously pointed at that file tree. If you
   discover a working tree with no `.git`, the correct recovery is
   **always** to clone GitHub first and overlay on top.

## Correct recovery recipe (the only supported one)

```powershell
# 1. Clone the canonical GitHub repo into a persistent C-drive folder.
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git C:\Python\project2_new

# 2. Overlay the current working files on top of the clone so that the
#    real .git history from the clone is preserved. Exclude .git, any
#    directory junctions (.venv, models, etc. that point outside the
#    tree), and anything that would pollute history.
robocopy C:\Python\project2 C:\Python\project2_new /MIR /XD .git .venv __pycache__ /XJ

# 3. Inspect the delta from the clone's HEAD and commit it on a
#    recovery branch. Make it obvious that the recovery branch is not
#    a fresh history — it is history extended from the real HEAD.
cd C:\Python\project2_new
git checkout -b recovery/<what-was-lost>
git status
git add <specific files, not -A, never -u>
git commit -m "chore(recovery): restore <what> from <source>"

# 4. Push the recovery branch IMMEDIATELY — before any further work.
git push -u origin recovery/<what-was-lost>
```

After the clone is anchored on GitHub you may swap `C:\Python\project2`
to the new tree (e.g. `move C:\Python\project2 C:\Python\project2_backup`
then `move C:\Python\project2_new C:\Python\project2`).

## Reconstructing code from reports

Sometimes the source is lost and only the release-final reports
remain (e.g. `C:\WaggleDance_ReleaseFinalRun\20260410_031819\reports\`).
When that happens:

- Do not pretend the reconstructed commit equals the original. State
  explicitly in the commit message that the work is reconstructed from
  reports, and list the report paths.
- Target the last known-good RC commit SHA and say so in the commit
  message. If you cannot equate byte-for-byte, state what may differ.
- Restore the regression tests from the same reports before you run
  the suite — a reconstruction without its tests proves nothing.

## Enforcement

- `tools\savepoint.ps1` refuses to run off the C: drive, refuses to run
  from a RAM-disk, runs the tests you pass, commits, and pushes in one
  safe step. Use it for every checkpoint.
- `AGENTS.md` and `CLAUDE.md` codify the rules for Codex and Claude
  agents. Future agents must read them before touching the repo.
