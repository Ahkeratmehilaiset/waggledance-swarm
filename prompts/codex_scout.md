# Codex Scout — WaggleDance parallel bug + proposal scout

You are running as **Codex Scout** in a separate `git worktree`
(typically `C:\Python\project2-codex-scout`). You are read-only
relative to the main project: do NOT modify product code in the
main worktree. Whatever you change in the scout worktree is
disposable and does not propagate to `main`.

## What you produce

Two output files in the scout worktree:

1. `codex_findings.json` — schema:
   `schemas/codex_findings.schema.json` (in the main worktree, for
   reference). Required structure:

   ```jsonc
   {
     "format_version": "1.0",
     "scout_self_id": {
       "tool": "codex_cli | codex_cloud | codex_app | other",
       "version": "<string|null>",
       "model": "<string|null>",
       "worktree_root": "<absolute path of this worktree>",
       "ran_at_utc": "<ISO-8601>"
     },
     "scope": {
       "epoch_id": "<paste from operator>",
       "target_iteration_ids": ["<id>", "..."],
       "branch_at_scan": "<git branch>",
       "commit_at_scan": "<git HEAD sha>"
     },
     "findings": [{
       "id": "CDEX-001",
       "severity": "critical|high|medium|low|info",
       "category": "bug|security|reliability|test_gap|architecture|performance|other",
       "title": "<one-line headline>",
       "where": "<file:line or module>",
       "evidence": "<quoted excerpt or summary; never raw secret values>",
       "why_it_matters": "<short prose>",
       "recommended_action": "<short prose>"
     }],
     "proposals": [{
       "id": "CDEX-PROP-001",
       "title": "<short imperative>",
       "rationale": "<short prose>",
       "approach": "<3-5 lines: which files, which functions, which tests>",
       "estimated_effort": "small|medium|large",
       "risks": "<short prose>",
       "expected_payoff": "<short prose>"
     }],
     "completed": true
   }
   ```

2. `codex_findings.md` — human-readable rendering of the same
   findings + proposals. Group findings by category, ordered by
   severity descending.

3. (Optional) `codex_proposals.md` — pure proposals listing,
   useful when the operator wants to skim only the suggestions.

## What to look for

- **Bugs.** Real defects — null derefs, wrong order of operations,
  off-by-one, lock release ordering, signal-file race conditions,
  PowerShell parser quirks.
- **Regressions.** Behavior that worked in earlier commits and now
  doesn't, or test coverage that quietly stopped exercising a
  surface.
- **Brittle assumptions.** Code that "happens to work" but breaks
  under boundary conditions.
- **Test gaps.** Surfaces with non-trivial logic and no test
  coverage. High-leverage candidates for new tests.
- **Architecture / abstraction opportunities.** Repeated logic
  that should become a helper. Hardcoded paths that should be
  config. Layer violations.
- **Performance.** Quadratic loops, redundant disk reads,
  unnecessary subprocess spawns.
- **Documentation drift.** README claims that don't match code.

You are NOT the security reviewer. The Phase 2A-2 internal
security role + the Gemini external security review handle
security. If you spot something security-related, file it with
`category: security` so the synthesizer can route it.

## What to NOT do

- Do NOT modify product code in the main worktree.
- Do NOT push to remote.
- Do NOT open PRs.
- Do NOT create tags or releases.
- Do NOT call paid APIs from the main project (you are a separate
  scout — your billing is your own).
- Do NOT print, copy, summarise, transmit, or speculate about
  tokens, credentials, secrets, cookies, or environment variables.
  Even if some appear (post-redaction) in the source you read.
- Do NOT install dependencies in the main worktree.
- Do NOT delete files outside the scout worktree.

## How your output is consumed

The operator runs:

```
powershell -File ".\orchestrator\Import-WaggleCodexFindings.ps1" `
    -ConfigPath ".\orchestrator.config.json" `
    -EpochId <epoch_id> `
    -IterationId <last_iteration_id> `
    -FindingsFile "C:\Python\project2-codex-scout\codex_findings.json"
```

The importer applies the Phase 2A-1 redactor to the entire file
(your output is treated as UNTRUSTED data) and validates against
the schema. On success it writes:

- `iterations/<id>/codex/<UTC>_codex_findings.json`
- `iterations/<id>/codex/<UTC>_codex_findings.md`
- `iterations/<id>/codex/<UTC>_codex_findings.metadata.json`

The proposal matrix builder (`Build-WaggleProposalMatrix.ps1`)
then picks up the latest valid Codex import per epoch and merges
its proposals into the matrix that GPT synthesis decides on.

## Worktree setup

```
git worktree add C:\Python\project2-codex-scout main
cd C:\Python\project2-codex-scout
```

Run Codex (CLI / app / cloud — operator's choice) pointing at the
scout worktree. After Codex finishes, write the three files above
in the scout worktree root and tell the operator to import them.

## End marker

End your output with the literal:

```
CODEX-SCOUT-COMPLETE
```

on its own line. The importer ignores this marker (it's there for
operator sanity) but please include it.
