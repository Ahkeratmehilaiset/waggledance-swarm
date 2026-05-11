# D1 PII scrub runbook — git history cleanup for H43/H44

**Audience**: operator executing the force-push. This document is
deliberately a runbook, not a code path — the destructive step is
operator-only per CLAUDE.md rule 9 (Claude may not force-push to main
without explicit confirmation at execution time).

**Authored**: 2026-05-11 as part of audit fix D1 (H43 + H44). PR-K.

**Status**: NOT YET EXECUTED. Run only after all open audit-fix PRs
land in `main` (currently #237–#248 + Codex's PRs). Force-push
orphans every clone, so we want a single coordinated cutover with
nothing in flight.

---

## What is being scrubbed

Audit found three pieces of operator personal/business data in
`configs/settings.yaml` (and a copy in `backup/2026-04-23/`) that
have been in git history since the project's early commits:

1. **Y-tunnus** (Finnish business ID, format `NNNNNNN-N`)
2. **Operator's full real name**
3. **Business name**

The values are intentionally not echoed in this runbook — the
operator knows what they are, and we don't want them archived in the
runbook itself.

Verification before run:

```bash
# Confirm the values are still in HEAD
grep -n "y_tunnus" configs/settings.yaml
grep -n "owner" configs/settings.yaml | head -3
grep -n "business_name" configs/settings.yaml | head -3
```

If those greps return matches, this runbook is needed. If they return
nothing, someone already removed them from HEAD; you still need to
filter the history.

---

## Prerequisites (BLOCKING)

1. **All open PRs landed.** `gh pr list` should return zero open PRs
   (or only PRs the operator explicitly plans to abandon). The
   force-push will rewrite every commit SHA in `main`, breaking any
   PR that branched off the old `main`.
2. **Codex paused.** Send a bridge `message` to Codex requesting they
   commit + push all in-flight worktrees and pause until the cutover
   completes. The 50+ worktrees in `git worktree list` will all
   become orphaned at force-push time.
3. **`git filter-repo` installed.** Test with:
   ```bash
   git filter-repo --version
   ```
   Install if missing: `pip install git-filter-repo`.
4. **Backup mirror exists.** This is non-negotiable — if anything
   goes wrong, this is the recovery point:
   ```powershell
   $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
   git clone --mirror https://github.com/Ahkeratmehilaiset/waggledance-swarm.git `
       "C:\backups\waggledance_pre_d1_$stamp.git"
   ```
5. **Operator on Anthropic-billed shell.** This is the operator's
   force-push; Claude can prepare and run filter-repo in a test
   clone, but the final `git push --force-with-lease` is operator-
   keyed per CLAUDE.md rule 9 caveat (force-push needs explicit
   per-call authorization).

---

## Step 1 — Dry-run on a test clone

We test the filter on a throwaway clone first to verify the
replacement actually scrubs every occurrence without breaking
syntactically-meaningful content (e.g. timestamps that happen to
match the Y-tunnus digit pattern).

```powershell
# Working directory: C:\Python\
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git project2-d1-test
cd project2-d1-test

# Create the replacement file OUTSIDE the repo so filter-repo
# doesn't rewrite it (it would rewrite committed copies of itself).
# Use the OS temp dir, not the project dir.
$replacements = "$env:TEMP\d1_replacements_$(Get-Random).txt"
@"
<ACTUAL_Y_TUNNUS>==>REDACTED_BUSINESS_ID
<OPERATOR_FULL_NAME>==>REDACTED_OWNER
<BUSINESS_NAME>==>REDACTED_BUSINESS
"@ | Out-File -FilePath $replacements -Encoding utf8

# Inspect: how many times does each value appear in history?
git log --all -p -S "<ACTUAL_Y_TUNNUS>" | Select-String "y_tunnus" | Measure-Object

# Run filter-repo against the test clone
git filter-repo --replace-text $replacements --force

# Verify: zero matches in any commit after scrub
git log --all -p -S "<ACTUAL_Y_TUNNUS>" | Measure-Object
# Expected: count = 0

# Verify the codebase still works on the scrubbed test clone
& C:\Python\project2-master\.python\Python313\python.exe -m pytest tests/test_hex_mesh.py -q
# Expected: same green pass-rate as on live repo
```

If anything in Step 1 fails (test breakage, residual matches),
**stop here**. Investigate before touching the live repo.

---

## Step 2 — Backup, then scrub the live repo

```powershell
# Working directory: C:\Python\project2-master (the live repo)

# 1. Tag the current HEAD as a recovery point on the LOCAL repo
git tag pre-d1-archive HEAD

# 2. Push that tag to origin as a backup branch
git push origin pre-d1-archive:refs/heads/pre-d1-archive
git push origin pre-d1-archive  # also as a tag

# 3. Run filter-repo against the live local clone
$replacements = "$env:TEMP\d1_replacements_$(Get-Random).txt"
# (same contents as Step 1 — re-create here so the temp file from
# Step 1 has not been deleted)
@"
<ACTUAL_Y_TUNNUS>==>REDACTED_BUSINESS_ID
<OPERATOR_FULL_NAME>==>REDACTED_OWNER
<BUSINESS_NAME>==>REDACTED_BUSINESS
"@ | Out-File -FilePath $replacements -Encoding utf8

git filter-repo --replace-text $replacements --force

# 4. Verify locally
git log --all -p -S "<ACTUAL_Y_TUNNUS>" | Measure-Object
# Expected: count = 0
```

---

## Step 3 — Force-push (the destructive step)

**This is the point of no return.** Once `--force-with-lease`
succeeds, every clone of this repo (including Codex's worktrees,
contributors' forks, CI runners, GitHub Actions caches) becomes
out-of-sync with `origin/main` and must re-fetch + reset.

```powershell
# Use --force-with-lease (NOT --force). If someone has pushed to
# main between Step 2 and this push, the push will REFUSE rather
# than overwrite their work. That is the safe behavior.
git push --force-with-lease origin main

# Also remove the backup branch from origin once scrub verified
# (keep the local tag forever as a recovery anchor):
# git push origin --delete pre-d1-archive
# ^ DO NOT run that until at least 48h post-scrub.
```

If `--force-with-lease` is rejected:
- Someone pushed to `main` between your Step 2 backup and now
- `git fetch origin && git log origin/main..HEAD` to inspect their commit
- Coordinate with them, re-run Step 2 starting from `git pull --rebase origin main`, then retry

---

## Step 4 — Post-scrub verification

```powershell
# 1. Verify GitHub history is clean
gh api repos/Ahkeratmehilaiset/waggledance-swarm/commits --paginate `
    --jq '.[] | "\(.sha) \(.commit.message | split("\n")[0])"' | Select-Object -First 20

# 2. Confirm origin/main tip matches your post-filter-repo HEAD
$local = git rev-parse HEAD
$remote = git ls-remote origin main | ForEach-Object { ($_ -split "`t")[0] }
if ($local -eq $remote) { "match: $local" } else { "MISMATCH local=$local remote=$remote" }

# 3. Verify the configs/settings.yaml on origin/main shows redacted values
gh api repos/Ahkeratmehilaiset/waggledance-swarm/contents/configs/settings.yaml `
    --jq '.content' | %% { [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_)) } | Select-String "y_tunnus|owner|business_name"
# Expected: REDACTED_* placeholders, no raw PII
```

---

## Step 5 — Re-sync downstream

Announce on the bridge (Claude can do this on behalf of operator):

```yaml
type: announcement
to: claude,codex,operator
severity: high
message: |
  D1 PII scrub force-pushed to main at <NEW_SHA>.
  All clones must re-fetch + reset:
    cd <clone-path>
    git fetch origin main
    git reset --hard origin/main
  Worktrees should be re-created from the scrubbed origin/main.
  Pre-scrub state preserved on local tag `pre-d1-archive` and on
  origin branch `pre-d1-archive` for 48 hours.
```

Codex worktrees specifically:
```powershell
# For each worktree under C:\tmp\wd-*:
foreach ($wt in (Get-ChildItem -Path C:\tmp\wd-* -Directory)) {
    cd $wt.FullName
    git fetch origin
    git reset --hard origin/main  # or operator-specified branch
}
```

---

## Recovery (if anything goes wrong)

The local `pre-d1-archive` tag and the origin branch
`pre-d1-archive` hold the pre-scrub state. To roll back:

```powershell
# Restore origin/main to pre-scrub state
git push origin pre-d1-archive:main --force
# Wait 180s, verify with `git ls-remote origin main`
# Then re-coordinate with Codex / contributors to re-sync
```

The mirror backup at `C:\backups\waggledance_pre_d1_<stamp>.git` is
the last-resort restore point — clone from there if origin/main is
unrecoverable.

---

## Alternative: HEAD-only fix (option B from PR-K design discussion)

If the operator decides the history scrub is too disruptive, the
less-destructive alternative is to fix only HEAD:

1. Move `facts.business_name`, `facts.owner`, `facts.y_tunnus` out
   of `configs/settings.yaml` into `data/operator_secrets.yaml`
   (gitignored).
2. Update any reader (none today — these fields are dead per audit
   H43) to read from the new location.
3. Land via normal PR.

This leaves the PII in git history (the cat is already out of the
bag for anyone who cloned in the past) but stops the leak going
forward. New clones starting today never see the values in HEAD.

The operator chose **option A (full history scrub)** in the audit-
discussion sync. This runbook is for option A. If they change their
mind, this PR can be discarded and option B implemented as a normal
HEAD-only PR.

---

## Status checklist (operator fills in)

- [ ] All open PRs merged or explicitly abandoned
- [ ] Codex paused via bridge message
- [ ] `git filter-repo` installed and tested with `--version`
- [ ] Backup mirror created at `C:\backups\waggledance_pre_d1_*`
- [ ] Step 1 dry-run on test clone — clean
- [ ] Step 2 local scrub — verified zero residual matches
- [ ] Step 3 force-push --force-with-lease — succeeded
- [ ] Step 4 verification — origin/main matches local HEAD
- [ ] Step 5 bridge announcement sent + Codex worktrees resynced
- [ ] (After 48h) backup branch `pre-d1-archive` removed from origin

---

**Last updated**: 2026-05-11 by Claude as part of PR-K (D1 prep).
