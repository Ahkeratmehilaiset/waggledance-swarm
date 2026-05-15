# Cottage Restart Agent Prompt - 2026-05-15

Use this as the first message for the next Codex/Claude session.

```text
We are continuing WaggleDance v3.13.0 release-prep from the 2026-05-15 cottage parking handoff.

Source of truth:
- GitHub main and persistent C-drive repo/worktrees only.
- Start in C:\Python\project2-master.
- Read docs/handoffs/2026-05-15-v3.13.0-cottage-session-parking.md first.
- Do not use temp/RAM/U: paths for source work.
- Use dedicated C:\Python\project2-worktrees\... worktrees for new write scopes.
- Use tools/savepoint.ps1 for every green checkpoint after staging only intended files.

Release rule:
- Do NOT cut stable, do NOT tag stable, do NOT bump release version.
- Release gate is expected HOLD:
  before_no_earlier_than_date, soak_window_incomplete, soak_evidence_missing.
  no_earlier_than=2026-05-24.

Immediate task:
1. Check PR #410:
   gh pr view 410 --json number,state,headRefOid,mergeable,mergeStateStatus,statusCheckRollup,url
2. PR #410 is ENG-06 advisory card:
   branch codex/eng06-advisory-card-20260515
   head a43a9cf268ab14681b14ea03a5759f56ca5c88ea
   files:
   - waggledance/core/v3_13_0/eng06_advisory_card.py
   - tests/v3_13_0/test_eng06_advisory_card.py
3. Wait for GitHub CI 5/5 SUCCESS and Claude RCO PASS on bridge task:
   claude-rco-pr410-eng06-advisory-card-2026-05-15
4. Merge exact head only:
   gh pr merge 410 --merge --delete-branch --match-head-commit a43a9cf268ab14681b14ea03a5759f56ca5c88ea
5. If local branch deletion fails because it is checked out in a worktree, verify GitHub state before doing anything else:
   gh pr view 410 --json number,state,mergedAt,mergeCommit,headRefOid,title,url
6. After merge:
   git pull --ff-only
   run postmerge sanity:
   .\.venv\Scripts\python.exe -B -m pytest tests\v3_13_0 -q -p no:cacheprovider --basetemp .pytest-tmp-postmerge-pr410-v313
   .\.venv\Scripts\python.exe -B -m pytest tests\contracts -q -p no:cacheprovider --basetemp .pytest-tmp-postmerge-pr410-contracts
   .\.venv\Scripts\python.exe -B -m pytest tests\tools -q -p no:cacheprovider --basetemp .pytest-tmp-postmerge-pr410-tools
   .\.venv\Scripts\python.exe -B tools\audit_v3_13_0_event_surface.py --count-only
   .\.venv\Scripts\python.exe -B tools\check_release_gate.py --allow-hold
7. Record bridge done/postmerge event.

After PR #410:
- Check bridge scout task claude-scout-next-operator-value-after-pr409-2026-05-15.
- Choose the next smallest operator-value PR.
- Likely options:
  a) ENG-06 CLI --render-card integration / snapshot route / output writer / docs.
  b) ENG-06 SQLite local takka-history adapter with strict local path and read-only SQL behavior.
  c) Read-only AIR-01 LAN indoor-air-sensor + knowledge bridge scout.
- Do not silently bypass private-host guardrails. Any LAN support must be explicit opt-in and fail-closed.

Codex strategic position on operator's auto-connect vision:
- The vision is right, but full zero-config auto-connect in anyone's environment is a staged substrate, not one PR.
- v3.x should ship safe first slices:
  L0 explicit file/URL/manual artifact.
  L1 explicit operator allowlist for private LAN read-only endpoints.
  L2 passive/low-risk discovery inventory, no reads without confirmation.
  L3 connector fingerprint -> normalized observation -> knowledge/persona interpretation.
  L4 writes/effects only behind separate operator approval and RCO gate.
- knowledge/ is the interpretation layer, not the discovery layer.
- Smallest safe first step after current park: AIR-01 explicit-host read-only LAN bridge with Digheran-shaped JSON fixture/parser and knowledge/air_quality threshold interpretation.
- Do not build generic LAN scanning before trust policy and tests exist.

Operator context:
- Operator is leaving the cottage; the session must be parked cleanly.
- Operator wants WD to solve real manual factory/cottage/home cases from local project material and knowledge/persona files, not abstract substrate polish.
- ENG-06 has shipped: solver core (#406), offline CLI (#407), burn-log adapter (#408), CLI adapter flags (#409). PR #410 is the advisory card.
```
