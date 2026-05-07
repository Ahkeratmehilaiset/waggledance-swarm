# Codex Scout setup

Phase 2B-Revision (ARCH-012) ships the *data-flow scaffold* for
Codex Scout. The orchestrator does not install Codex, run Codex,
or call any Codex API. The operator runs Codex manually in a
disposable `git worktree` and then imports the findings into the
main project.

## Prerequisites

* A Codex tier the operator already pays for: Codex CLI (Plus /
  Pro / Business / Edu / Enterprise), the Codex desktop app, or
  Codex cloud.
* PowerShell 5.1+ (the importer is PS 5.1 compatible).
* This repo on a real C-drive path (per CLAUDE.md golden rule 1 —
  no RAM disk, no zip extraction).

## Set up a disposable scout worktree

```
git worktree add C:\Python\project2-codex-scout main
cd C:\Python\project2-codex-scout
```

The scout worktree starts as a clean snapshot of `main`. It is
disposable: whatever Codex changes there does not propagate back
to `main`. Discard it between epochs or keep it around — your
choice.

## Run Codex Scout

The prompt template is `prompts/codex_scout.md` (in the main
worktree, but mirrored into the scout worktree by `git worktree
add`). It instructs Codex to:

* read the codebase + the latest epoch evidence
* find bugs, regressions, brittle assumptions, missing tests,
  high-leverage improvements
* write `codex_findings.json` + `codex_findings.md` (+ optional
  `codex_proposals.md`) in the scout worktree root
* NOT modify product code in the main worktree
* NOT push, PR, tag, or release

### Example invocations

Codex CLI:

```
cd C:\Python\project2-codex-scout
codex --prompt .\prompts\codex_scout.md
```

Codex desktop app: open the app, point at
`C:\Python\project2-codex-scout`, paste `prompts/codex_scout.md`.

Codex cloud: launch a cloud task with the same prompt and
worktree (mount the path the platform expects).

## Import results

After Codex writes `codex_findings.json` (and optional .md
companions) in the scout worktree, run from the main project:

```
powershell -File ".\orchestrator\Import-WaggleCodexFindings.ps1" `
    -ConfigPath ".\orchestrator.config.json" `
    -EpochId <epoch_id> `
    -IterationId <last_iteration_id> `
    -FindingsFile "C:\Python\project2-codex-scout\codex_findings.json"
```

The importer:

* applies the Phase 2A-1 redactor to the entire findings file
  (Codex output is UNTRUSTED data, may contain quoted secrets);
* validates against `schemas/codex_findings.schema.json`;
* verifies `scope.epoch_id` matches the `-EpochId` argument;
* writes `iterations/<id>/codex/<UTC>_codex_findings.{json,md,metadata.json}`
  + a stable `iterations/<id>/codex/findings.json` copy used by
  the proposal-matrix builder.

On schema violation the importer writes
`<UTC>_codex.invalid.txt` and `<UTC>_codex.invalid.metadata.json`
with an explicit `reason` field; the matrix builder skips it.

## Where the findings flow

After import, the proposal-matrix builder
(`orchestrator/Build-WaggleProposalMatrix.ps1`) picks up the
latest valid Codex import per epoch and merges its proposals
into the matrix that GPT synthesis decides on.

The synthesis paste-block (P9) inlines a "Codex" section so GPT
sees Codex's findings + proposals alongside Claude internal
reviews and external Gemini/Grok reviews.

The synthesis prompt explicitly weights Codex with caution: it is
a *parallel scout*, not a primary reviewer. But its angles are
sometimes valuable.

## Worktree cleanup

```
git worktree remove C:\Python\project2-codex-scout
```

Or just leave the worktree around. It does not auto-update from
`main`, so re-running Codex on a stale worktree gives stale
findings. Either prune it between epochs or do a `git pull` in the
worktree before re-running Codex.

## What this does NOT do

* Does NOT install Codex.
* Does NOT run Codex from the orchestrator.
* Does NOT consume Codex's API tokens from the main project.
* Does NOT include Codex's output in the PR — runtime data files
  under `iterations/` are local-only by repo convention.

If Codex is unavailable for an epoch, just skip the Codex steps.
The proposal matrix simply has no Codex source rows; the rest of
the pipeline keeps working.
