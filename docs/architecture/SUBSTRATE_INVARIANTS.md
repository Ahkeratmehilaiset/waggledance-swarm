# Substrate Invariants

This document records cross-cutting invariants that the agent-bridge
substrate has discovered empirically. Each invariant is a rule that, if
broken, will produce CI failures, false-positive bridge state, or wedge
multi-agent operation. The rules here are observed-in-practice, not
designed-up-front.

## Conventions

* Invariants are numbered `INV-NN` and never renumbered.
* Each invariant records: the rule, the empirical observation that produced
  it, the failure mode it prevents, and the operator action that closes the
  loop.
* If an invariant is later proven too narrow or wrong, mark it `(deprecated)`
  with the replacement number — never delete an entry.

## INV-01 — Rebase before merge after any clock-fix lands

**Rule.** Once a PR that changes the test or production clock surface
(wall-clock defaults, time-mock wrappers, deterministic time helpers) is
merged, every other open PR MUST rebase on the new main before merge. Do
not auto-merge any PR whose CI ran before the clock fix landed.

**Empirical observation.** 2026-05-18:

* PR #482 (Slice 8d, `archive_stale_claims`) shipped a test that combined a
  fixed `_now() = 12:00Z`, `_stale_now() = 13:00Z`, and `claim_task` whose
  default `now_utc` fell back to real wall-clock.
* The test passed at commit time because real UTC was `< 13:00Z`.
* After real UTC passed `13:00Z`, the test created claim files with real
  heartbeats `> 13:00Z`, so `_stale_now()`-comparisons saw heartbeats
  *newer* than the cutoff. Archive returned `[]`, assertion failed.
* PR #485 fixed the test (deterministic wrapper, `now_utc=_now()`).
* PR #486 was branched before PR #485 merged. Its CI ran the now-broken
  test from main and failed three of five checks despite PR #486 itself
  only touching `tools/bridge_next_action.py`.
* Rebase of PR #486 onto main-with-#485 produced a green CI run.

**Failure mode prevented.** "PR is correct, RCO is PASSED, CI fails on
unrelated tests" — agents waste time debugging code that isn't broken.

**Operator action.** When merging a clock-fix PR:

1. Note the merge commit SHA in the bridge release event.
2. List all other open PRs.
3. Either rebase each one immediately, or signal to its author that
   rebase is required before merge.

The repeatable queue check is:

```powershell
python tools/report_open_pr_stale_base_queue.py --expected-base-sha <current-main-sha> --repo OWNER/NAME --json
```

The report is read-only: it compares each open PR's `baseRefOid` to the
expected current main SHA and does not refresh branches, post bridge events,
or authorize merges.

## INV-02 — Status matching is by whole token, never substring

**Rule.** Any code that classifies bridge events by status string MUST
tokenize the status on `[^a-z0-9]+` and compare whole tokens. Substring
matching produces false positives that survive both author tests and
post-merge smoke testing.

**Empirical observation.** 2026-05-18, PR #486:

* `bridge_next_action`'s open-request detector used
  `"ready" in status` substring match.
* A wake-ack with status
  `wake_ack_corrected_rco_pass_already_posted_clear_to_merge` was
  classified as open because `"already"` contains the substring `"ready"`.
* This shipped through PR #483 RCO PASS, my own use of the tool, and only
  manifested as a stale recommendation in live operation.

**Failure mode prevented.** Free-form status strings have ~unbounded
substring collisions. Author tests cannot cover the space; only live
traffic exposes them.

**Operator action.** Reject any substring-based status match in code
review. Require the tokenizing helper (e.g.
`tools/bridge_next_action.py::_status_has_any`) or its equivalent.

## INV-03 — Force-push to a peer agent's branch requires explicit
  authorization

**Rule.** Force-pushing to a branch owned by another agent (or to any
shared branch) is a destructive operation. It must not happen without
explicit authorization from the operator OR from the branch-owning
agent over the bridge.

**Empirical observation.** 2026-05-18: PR #486 needed rebase to absorb
PR #485's fix. Codex was auth-blocked and could not rebase its own
branch. Claude could rebase locally and force-push, but doing so
without bridge authorization would have overwritten Codex's work.
Operator explicitly authorized via "Korjaa nama kaikki" — only then
did the force-push (with `--force-with-lease`) proceed.

**Failure mode prevented.** Silent loss of in-flight peer work. Even
with `--force-with-lease`, a force-push removes commits from the visible
history and may surprise the branch owner.

**Operator action.** When an auth-blocked peer asks for a rebase
assist via the bridge, treat the request itself as the authorization
for that specific branch and base. Do not extend it implicitly to other
branches.

## INV-04 — Shared-worktree HEAD-switch is a real concurrency hazard

**Rule.** When multiple agent sessions share a single git worktree
(`.git/HEAD` is one file), the HEAD ref can shift unexpectedly under
either session. Sessions MUST verify their branch via
`git branch --show-current` (or equivalent) before any operation that
depends on HEAD. Do not assume the branch you set earlier is still
checked out.

**Empirical observation.** 2026-05-18: both Claude and Codex sessions
observed ≥3 unexpected branch switches today. Examples:

* Claude opened a feature branch with `git checkout -b claude/...`,
  later committed, and the commit landed on a Codex branch instead.
* Codex's session reported that "shared worktree keeps switching to
  claude/work-queue-archive-stale-v1-2026-05-18".

**Failure mode prevented.** Commits on wrong branch; PR opened from
unintended HEAD; force-push targeting the wrong upstream. None of these
are corrupting if caught, but each costs minutes of recovery.

**Operator action.** This is a confirmed operational hazard. Mitigations
available now:

1. Re-assert your branch before every `git commit`, `git push`, or
   `gh pr create`.
2. Prefer `git switch -c <name>` over `git checkout -b <name>` (clearer
   semantics, refuses unsafe switches).
3. After any cross-branch operation (`git fetch`, `git show`,
   `gh pr view`), explicitly `git switch` back to your working branch
   before resuming work.
4. For every new write-capable agent session, start through the dedicated
   worktree bootstrap:

   ```powershell
   cd C:\Python\project2-master
   . .\.agent-bridge\bin\Start-AgentBridgeWorktreeSession.ps1 -Agent codex
   ```

   Claude uses the same command with `-Agent claude`. This creates or
   reuses a physical per-agent worktree, then calls
   `Start-AgentBridgeSession.ps1 -RequireDedicatedWorktree` inside it.

The cooperative branch guard remains useful for manual maintenance, but the
worktree bootstrap is the structural mitigation for autonomous parallel
implementation.

## Maintaining this file

* Add new invariants only when an empirical failure has confirmed them.
* Include the specific PR / date / commit SHA that produced the
  observation, so future readers can trace it.
* If an invariant is later proven too narrow or wrong, mark it
  `(deprecated)` with the replacement number — never delete an entry.
