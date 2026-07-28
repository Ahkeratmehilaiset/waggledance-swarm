# Cottage Restart Agent Prompt - 2026-05-15

Use this as the first message for the next Codex/Claude session.

```text
We are continuing WaggleDance v3.13.0 release-prep from the 2026-05-15 cottage parking handoff.

Source of truth:
- GitHub main and persistent C-drive repo/worktrees only.
- Start in C:\Python\project2.
- Read docs/handoffs/2026-05-15-v3.13.0-cottage-session-parking.md first.
- Do not use temp/RAM/U: paths for source work.
- Use dedicated C:\Python\waggledance-agent-worktrees\... worktrees for new write scopes.
- Use tools/savepoint.ps1 for every green checkpoint after staging only intended files.

Release rule:
- Do NOT cut stable, do NOT tag stable, do NOT bump release version.
- Release gate is expected HOLD:
  before_no_earlier_than_date, soak_window_incomplete, soak_evidence_missing.
  no_earlier_than=2026-05-24.

Immediate task:
1. Check that PR #410 is already merged:
   gh pr view 410 --json number,state,mergedAt,mergeCommit,headRefOid,title,url
   Expected merge commit: db4ef940be364bd93ce338c24419735af50452cc.
2. Confirm PR #410 postmerge sanity is already recorded on bridge task:
   postmerge-sanity-after-pr410-2026-05-15
   Expected result:
   - tests/v3_13_0: 532 passed, 1 skipped
   - tests/contracts: 486 passed
   - tests/tools: 37 passed
   - event surface: 49
   - release gate: HOLD
3. Finish this handoff if PR #411 is still open:
   gh pr view 411 --json number,state,headRefOid,mergeable,mergeStateStatus,statusCheckRollup,url
   Verify CI and Claude RCO after the latest commit, then merge exact current head.
4. Treat PR #412 as complementary Claude long-form handoff/history:
   gh pr view 412 --json number,state,headRefOid,mergeable,mergeStateStatus,statusCheckRollup,url
   Review separately; do not merge just because PR #411 is ready.

After the handoff is on main:
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
