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

## Operational discipline (added 2026-04-28, post-Phase-10)

These rules are the result of Phase 9 / Phase 10 release lessons. They are not
optional; any future Claude Code session that lands work in this repo must
follow them.

### 6. PR-only — no direct push to main

**All commits land via PR.** No direct push to `main`, even for docs-only or
"trivial" changes. There is no carve-out for typo fixes, README polish, or
state-file updates. The PR gate is the only landing surface.

* If a session's work is docs-only, it still goes through a PR.
* If a session believes a change is so trivial it doesn't warrant review,
  the session is wrong about that. Open the PR.
* Branch protection on `main` may not enforce this for every actor; the
  rule is operator-side regardless of what the server enforces.

### 7. Push verification — never classify push as failed before 180s

`git push` from the Claude Code shell can complete asynchronously. The
v3.6.0 and Phase 10 release sessions both initially classified pushes as
"silently_backgrounded" within 10 seconds; both pushes had in fact reached
the remote and the classification was a false positive that produced
unnecessary stop-and-handoffs.

**The contract:**

* After any `git push`, do NOT classify failure earlier than 180 seconds.
* Verify with `git ls-remote origin <branch>` every 15 seconds for up to
  180 seconds before deciding the push is blocked.
* Do not stack parallel push retries while a prior push is still in flight.
* Once the remote tip matches the local tip, the push has succeeded —
  treat any earlier "no output" as harmless.

### 8. Strongest-model default

When a session has a model choice, the default is the strongest available
Claude Opus model in this repo's coding/teaching lane (currently
`claude-opus-4-7`). This applies to:

* the active session model;
* any subagent the session spawns whose model is not explicitly fixed;
* any `ClaudeCodeBuilder` invocation under
  `waggledance/core/providers/claude_code_builder.py`.

If a fallback to a smaller model occurs, it must be logged explicitly in
the session state file's `fallback_events`. Anthropic / OpenAI / local
provider lanes remain supported peers; no lane should be over-claimed as
"already implemented" if it is not.

### 9. Autonomous-merge guardrails

A Claude Code session MAY autonomously create PRs, wait for CI, and
squash-merge them WITHOUT a fresh approval prompt **only if all four**:

a) PR head SHA matches the local `EXPECTED_HEAD`,
b) all required CI checks are green,
c) GitHub mergeable state is `clean` / `mergeable`,
d) no rule in this file (or in `wd_phase*_master_prompt*.md`) is violated.

Use `gh pr merge --match-head-commit="$EXPECTED_HEAD"` to refuse stale-SHA
merges. Never `--admin`, never `--no-verify`, never force-push.

### 10. Atomic-flip discipline

The atomic runtime flip ("Stage-2 cutover") is a separate risk domain.

* Do NOT execute the cutover in design / build / docs sessions.
* Do NOT collect `HUMAN_APPROVAL.yaml` during design / build / docs sessions.
  Approval is one-shot and belongs only to the actual cutover execution
  session — the operator signs once at execution time.
* Do NOT prompt the operator for "approval keys" or signatures during
  ideation, RFC authoring, or refactoring.
* Bringing forward a previously-collected-then-SUPERSEDED approval as an
  audit artifact (header explicitly says `*** SUPERSEDED — DO NOT EXECUTE ***`)
  is preservation, not collection, and is allowed.
* The cutover mechanism is specified in
  `docs/architecture/STAGE2_CUTOVER_RFC.md`. A real cutover session
  reads that RFC, the `00_README.md` SUPERSEDED block, and the
  `HUMAN_APPROVAL_V2.yaml.draft` (when authored) before doing anything.

### 11. Trivial-rationalization warning

"It's just docs" / "it's just a typo" / "it's just one line" is not a
license to bypass the PR gate, the truth review, or any rule above.
Docs-only PRs still:

* go through PR review;
* run targeted tests if they touch any tested doc invariant
  (e.g., `tests/phase10/test_truth_regression.py`);
* respect MAGMA / FAISS / control-plane truth (no doc edits that imply a
  runtime read path that the code does not actually take).

If a session catches itself reasoning "this is trivial, I'll commit
direct," that is the moment to stop and open a PR.

## What this file does NOT override

- `AGENTS.md` task rules still apply.
- `.gitignore` still applies.
- The project's existing build, test, and release processes still apply.
- `wd_phase*_master_prompt*.md` per-session rules still apply on top of
  this file. Per-session rules can be more strict; they cannot loosen
  the rules above.

## When in doubt

- Stop.
- Verify you are on the C: drive.
- Verify `git remote -v` points at the real GitHub repo.
- Verify `git status` is clean or that your in-progress work is staged.
- For pushes: run `git ls-remote origin <branch>` and wait up to 180s
  before classifying anything as blocked.
- For merges: verify `EXPECTED_HEAD == origin head` before
  `gh pr merge --match-head-commit`.
- Then proceed.
