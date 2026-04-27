# GitHub Release Status — v3.6.0

**Release URL:** https://github.com/Ahkeratmehilaiset/waggledance-swarm/releases/tag/v3.6.0

**Release outcome:** SUCCESS

| Field | Value |
|---|---|
| Tag | `v3.6.0` |
| Title | "v3.6.0 — Phase 9 Autonomy Fabric" |
| Latest | yes (`--latest` flag) |
| Notes source | `docs/runs/release_notes_draft.md` |
| Tagged commit | `a1c41528e4694094543be30ab641b362c968914d` (squash-merge of PR #51) |

## Pre-tag state

| | |
|---|---|
| Branch tip before merge | `phase9/autonomy-fabric @ fad731d` (verified head SHA) |
| main tip before merge | `d9a6dce` |
| PR #51 | OPEN, MERGEABLE, CLEAN, 5/5 CI SUCCESS |

## Merge details

| | |
|---|---|
| Method | `gh pr merge 51 --squash --match-head-commit=fad731d59...` |
| Squash subject | `feat(release): Phase 9 — Autonomy Fabric (v3.6.0)` |
| Body source | `docs/runs/release_notes_draft.md` |
| Squash commit on main | `a1c41528e4694094543be30ab641b362c968914d` |
| Merged at (UTC) | `2026-04-27T12:36:32Z` |
| Merged by | `Ahkeratmehilaiset` |
| Branch deletion | NOT performed (release branch preserved as evidence + base for follow-up phase8.5 PRs) |

## Post-tag state

| | |
|---|---|
| main tip | `a1c41528e4694094543be30ab641b362c968914d` (advanced from `d9a6dce`) |
| v3.6.0 tag | pushed to origin |
| GitHub release | published (latest=true) |
| pyproject.toml version | `3.6.0` (verified on main) |
| waggledance.__version__ | `3.6.0` (verified on main) |
| LICENSE-BUSL.txt Change Date | `2030-03-19` |

## What was NOT done (per spec)

- Atomic runtime flip — explicitly deferred to Prompt 2 (separate session)
- Phase 8.5 follow-up PRs — deferred per audit decision
- Branch deletion — not requested
- History rewrite — explicitly forbidden under Strategy A
- Force push — explicitly forbidden in this session

## Next operator actions

Per `docs/runs/release_bundle_2026_04_27/post_merge_checklist.md`:

1. (within 1 hour) — fetch + verify main on operator's local clones
2. (same day) — open follow-up PR for `phase8.5/vector-chaos → main` (R7.5)
3. (within a week) — phase8.5 follow-up chain in dependency order:
   - `phase8.5/vector-chaos`
   - `phase8.5/curiosity-organ`
   - `phase8.5/self-model-layer`
   - `phase8.5/dream-curriculum`
   - `phase8.5/hive-proposes`
4. (after all phase8.5 land + 400h campaign frozen) — author signed `HUMAN_APPROVAL.yaml` per `docs/atomic_flip_prep/03_HUMAN_APPROVAL.yaml.draft` and run Prompt 2 atomic flip session.
