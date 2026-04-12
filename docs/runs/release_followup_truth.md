# Release Follow-up — Truth Intake

- **Date:** 2026-04-12
- **Operator:** Claude Code (Opus 4.6)

## Truth Table

| Item | Value |
|---|---|
| Current branch | `hardening/post-v3.5.7-ui-gauntlet` |
| main HEAD | `ce282af` (merge: v3.5.7 — Honest Hologram Release) |
| hardening HEAD | `299ca9f` (hardening: UI gauntlet harness + Phase A-F results) |
| Latest v3.5.* tags | v3.5.0 through v3.5.7 |
| v3.5.7 tag exists locally | Yes, on `88c91db` |
| v3.5.7 tag exists on GitHub | Yes (verified via `git ls-remote`) |
| v3.5.7 GitHub release | Exists (created 2026-04-12) |
| pyproject.toml version | 3.5.7 |
| CHANGELOG top entry | [3.5.7] — 2026-04-12 — Honest Hologram Release |
| Worktree clean (code) | Yes — untracked files are docs/reports/screenshots only |
| `gh` CLI available | No — not installed |

## Source Documents Verified

| Document | Key Fact |
|---|---|
| final_release_handoff.md | v3.5.7 shipped, tag on `88c91db`, merge on `ce282af` |
| promotion_latest.md | project2_new promoted to project2 via robocopy, 662 files written |
| ui_gauntlet summary.md | 477 queries, 100% success, 0 XSS, 0 breaks |
| ui_fidelity_baseline.md | 33/33 viewport/tab checks pass |
| fault_drills.md | 6/7 pass, 1 inconclusive (test infra) |
| mixed_soak.md | 30min soak, 36 cycles, 0 errors, backend 100% up |
| findings.json | All gates PASS |
| PHASE7_FIXES.md | 3 fixes (HOLO-001, NEWS-001/002/003, WIRE-001), 20 new tests |
| PHASE9_FINAL_VERIFY.md | 5378 tests pass, 15/15 endpoints green |
| FINAL_RELEASE_GATE.md | READY FOR RC (soak was 10.24h vs 12h mandate) |
| FINAL_RELEASE_HANDOFF.md | Operator checklist for 12h re-soak + version bump |
| OVERNIGHT_SOAK_FINAL.md | 306/360 ticks, all green, items +161 |
| README.md | Version badges say 5358 tests (pre-Phase 7 count) |
| CHANGELOG.md | v3.5.7 entry exists, comprehensive |
| docs/API.md | Shows v3.5.6 version, missing hologram cookie auth docs |
