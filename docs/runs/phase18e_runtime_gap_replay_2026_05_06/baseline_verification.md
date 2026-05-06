# Phase 18E — P0 Baseline Verification

**Date (UTC):** 2026-05-06
**Operator worktree:** `C:\Python\project2-master`
**origin URL:** `https://github.com/Ahkeratmehilaiset/waggledance-swarm.git`

## origin/main

`7d1dedef8e29482ff2f8938af484d681e10ca98f` — matches the operator-reported expected SHA (Phase 18D PR #85 squash-merge, 2026-05-05T21:55:09Z).

## Tag SHAs (8 prior tags — must remain unchanged through Phase 18E)

| Tag | Commit SHA | Status |
| --- | --- | --- |
| `v3.8.0` | `824176ebf2a6b8debed41982090a125cbe2ddad1` | GitHub **Latest**, isPrerelease=false |
| `v3.9.0-producer-fabric-alpha` | `c726995c816ee4c09e031c2190c3de6592e82879` | Pre-release |
| `v3.9.1-local-efficiency-benchmark-alpha` | `f4d0a4a4152ca74e98a8d7f7161c233075bf4111` | Pre-release |
| `v3.9.2-local-ollama-baseline-alpha` | `db5d7db1ecb9ae6f17293f0bf7261f4c9d40e91c` | Pre-release |
| `v3.9.3-local-model-sweep-alpha` | `d0704efe46be18d480ed425ff83b087cd36ef9bd` | Pre-release |
| `v3.10.0-benchmark-schema-alpha` | `4554b24a47045ab10c1c0fbcb010f695d47d867c` | Pre-release |
| `v3.10.1-gap-miner-feedback-alpha` | `b408b14a4209ee9f8da00f040223a988815d0f87` | Pre-release |
| `v3.10.2-mined-solver-dispatch-alpha` | `e9aa1de10376109987a7c18d331bbcc996a9ddf9` | Pre-release |

`gh release view v3.10.3-runtime-gap-replay-alpha` → `release not found` (expected; Phase 18E candidate not yet created).

## GitHub release ordering (top of `gh release list --limit 12`)

`v3.10.2-mined-solver-dispatch-alpha` is the most recent Pre-release; `v3.8.0` is `Latest`. All 7 alphas above v3.8.0 are Pre-release.

## Secret-hygiene posture (P0 finding + remediation)

**Initial scan finding:** `git config --get-regexp 'branch\..*\.remote'` revealed **9 branch upstream entries** containing an embedded `https://x-access-token:gho_...@github.com/...` URL — an artifact of the token-in-URL fallback used during prior phases (17C / 17D / 18A / 18B / 18C). Per master-prompt P0 secret hygiene, this is a STOP fail-closed condition.

**Token status:** the embedded token value is the one the operator reported as **revoked** before Phase 18D began. Authentication-wise it is dead. Hygiene-wise it remained as a string in local config.

**Remediation (this session, before any further P0 work):** all 9 affected `branch.<name>.remote` keys were rewritten in place via `git config --replace-all <key> origin` (without reading the leaked value into shell variables or printed output). The cleanup did not modify `.git/config` on any other worktree, did not touch any tag, did not touch any remote. After cleanup:

* `git config --list | grep -cE '(x-access-token|gho_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})'` → **0**
* `git config --get-regexp 'branch\..*\.remote|branch\..*\.merge|remote\..*\.url'` shows no token-bearing values.

**Affected branch keys (cleanup record, values redacted):**

* `branch.phase17c/local-ollama-baseline.remote` → `origin`
* `branch.phase17c/post-release-docs.remote` → `origin`
* `branch.phase17d/local-model-sweep.remote` → `origin`
* `branch.phase17d/post-release-docs.remote` → `origin`
* `branch.phase18a/benchmark-externalization-schema.remote` → `origin`
* `branch.phase18a/post-release-docs.remote` → `origin`
* `branch.phase18b/gap-miner-feedback.remote` → `origin`
* `branch.phase18b/post-release-docs.remote` → `origin`
* `branch.phase18c/mined-solver-runtime-dispatch.remote` → `origin`

These branches were squash-merged on origin long ago; the rewrite affects only the local upstream pointer and means future `git push` on those branches will go through the standard `origin` URL with the credential helper (Windows Credential Manager / `gh` keyring), not via a token-bearing URL.

## `gh auth status`

```
github.com
  ✓ Logged in to github.com account Ahkeratmehilaiset (keyring)
  - Active account: true
  - Git operations protocol: https
  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'
```

(`gh auth status` reports the scope set; it does not print the token value.)

## `git remote -v`

```
origin  https://github.com/Ahkeratmehilaiset/waggledance-swarm.git (fetch)
origin  https://github.com/Ahkeratmehilaiset/waggledance-swarm.git (push)
```

Clean.

## Verdict

P0 GREEN, with a hygiene remediation noted: 9 stale token-bearing branch upstream entries cleaned out of local Git config. The token value was already revoked by the operator; cleanup removed the leak surface from local Git state so subsequent push commands cannot re-leak it through process listings or shell history. Phase 18E may proceed to P1 inventory.
