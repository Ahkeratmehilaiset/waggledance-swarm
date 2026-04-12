# Release Follow-up — Final Handoff

- **Date:** 2026-04-12
- **PATH:** DOC_SYNC_ONLY
- **Version:** v3.5.7 (unchanged — no bump)

## Execution Summary

| Step | Result |
|---|---|
| Phase 0: Truth intake | 15+ docs read, truth table written |
| Phase 1: Diff classification | PRODUCT=EMPTY → PATH=DOC_SYNC_ONLY |
| Phase 2: Docs sync | README, CHANGELOG, API.md updated; index created |
| Phase 3: Validation | 20/20 tests, 6/6 smoke endpoints |
| Phase 4A: Merge | `hardening/post-v3.5.7-ui-gauntlet` → `main` (no-ff) |
| Phase 4A: Push | `main` pushed to origin (`ce282af..1a1af23`) |
| Phase 4A: GitHub release body | NOT UPDATED — `gh` CLI unavailable, manual step needed |

## Commits

| SHA | Message |
|---|---|
| `8c58e6d` | `docs: sync GitHub and repo narratives with shipped reality` |
| `1a1af23` | `merge: hardening/post-v3.5.7-ui-gauntlet → main (docs-sync only)` |

## What changed in docs-sync

- **README.md**: test badge 5358→5378, added version badge v3.5.7, updated Current Status table (version, test count, soak, UI hardening line)
- **CHANGELOG.md**: added "Unreleased — Post-v3.5.7 Hardening" section documenting UI gauntlet results, harness code, and tool version bumps
- **docs/API.md**: documented hologram cookie auth bootstrap, `/api/chat` auth behavior (401+WWW-Authenticate, 422, `message` alias), `/api/feeds` v3.5.7 semantics (per-source state, honest freshness), fixed version refs 3.5.6→3.5.7
- **docs/runs/release_followup_index.md**: top-level index linking all release pipeline and hardening reports

## What did NOT change

- No version bump (v3.5.7 stays)
- No new tag
- No new GitHub release
- No runtime code (`waggledance/`, `web/`, `configs/`, `start_waggledance.py`)
- No `pyproject.toml` or `requirements.txt` changes

## Manual action required

**Update the GitHub release body** for `v3.5.7` (release ID `307972297`) to append the post-release hardening summary. The `gh` CLI is not installed on this machine. Do one of:

1. Install `gh` and run:
   ```bash
   gh release view v3.5.7 --json body -q .body > /tmp/body.md
   # Edit /tmp/body.md to append hardening summary
   gh release edit v3.5.7 --notes-file /tmp/body.md
   ```
2. Or edit directly at: https://github.com/Ahkeratmehilaiset/waggledance-swarm/releases/tag/v3.5.7

## Reports index

All reports are linked from [`docs/runs/release_followup_index.md`](release_followup_index.md).
