# Phase 2B-Revision baseline

**Captured:** 2026-05-07 (UTC)
**Driver prompt:** `prompts/phase2b_revision_cockpit_codex_regression.md`

## Branch + commits

| Item | Value |
|------|-------|
| Current branch | `orchestrator/phase2b-cross-vendor-iteration-cycle` (will rename to `orchestrator/phase2b-revision-cockpit-codex-regression` at P14) |
| HEAD SHA at P0 | `efe25530aa3f80cf631759777fffec999cf9e8b7` |
| Parent (`origin/main`) | `39f15fd710c5b9e7cfd7e4b8e6829c9e6b72248a` (Phase 2A-5 PR #93) |
| Preflight commit | `efe2553` — `phase2br: preflight Phase 2B baseline + F-001 config path overrides` |

## Hardening gates

* Self-test: PASS (path-generation only)
* Full 24-gate run at `docs/runs/hardening_gates/latest.json`: 24/24 PASS

## Worktree state

* Working tree clean immediately after P-1 preflight commit.
* Local-only excludes added to `C:/Python/project2/.git/info/exclude`
  to keep validation/runtime/unrelated paths out of `git status`:
  * `docs/runs/orchestrator_phase2b_validation_2026_05_07/`
  * `iterations/_phase2b_validation/`
  * `orchestrator.config.phase2b_validation.json`
  * `/raportti.md` (transient validation output)
  * older-phase post_merge / pr_body docs
  * operator-only `prompts/phase2*_*.md` working drafts

## Phase 2B handoff requirements

`prompts/phase2b_handoff_requirements.md` (committed by Phase 2A-5
PR #93). Each clause is achievable in this phase:

* **Ledger maintenance** — Phase 2BR will reserve ARCH-010..013,
  REL-012/013/014, SEC-009 in P1 and promote them to `fixed` in
  P12.
* **Phase-agnostic hardening-gate report path** — already enforced
  by Phase 2A-5; P10 will use the default
  `docs/runs/hardening_gates/<utc>.json`.

## Next available ledger tag numbers

From the current `phase_fix_ledger.json`:

* ARCH max = 009 → next ARCH-010
* REL max = 011 → next REL-012
* SEC max = 008 → next SEC-009

This phase reserves 8 entries across these families.
