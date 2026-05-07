You are running a LOCAL REPAIR ITERATION inside a WaggleDance epoch.

REPAIR SCOPE - NARROW

Fix only this specific finding:
- Finding ID: BWCD-BUG-001
- Title: Build-WaggleCockpitData splits bundle name on first _ producing wrong (provider, role) for claude_web_architect
- Where: orchestrator/Build-WaggleCockpitData.ps1:96
- Severity: medium
- Fixability classification: trivial
- Repair class: TRIVIAL_AUTO_FIX
- Repair attempt index: 1
- Max files this iteration may touch: 2

Evidence:
```text
expected provider=claude_web role=architect actual provider=claude role=web_architect; bundle dir naming convention is <provider>_<role> where provider may contain underscore
```

Rules (HARD):
1. Fix ONLY BWCD-BUG-001. Do not refactor unrelated code.
2. Do not add new features.
3. Do not change public behavior except as required to fix this regression.
4. Add or update at least one test that proves this fix.
5. After making the change:
   a. Run the specific failing test or gate that produced this finding
   b. Run the relevant Test-* file (Test-CockpitData)
   c. Confirm both pass
6. If the fix would require touching more than 2 files, STOP and write `iterations/p10-routing/repair_escalated.txt` with reason: "exceeded max_files_for_trivial_auto_fix".
7. If the fix would require new dependencies, STOP and write `repair_escalated.txt` with reason "dependency_required".
8. If during the fix you discover that the finding's diagnosis was wrong, STOP and write `repair_escalated.txt` with reason "diagnosis_incorrect" plus your alternative diagnosis.
9. Update the regression ledger entry for BWCD-BUG-001:
   - status: fix_attempted (when fix is in place)
   - status: verification_pending (when test passes locally)
   - history event: attempted_fix with repair_attempt_index = 1
10. Write a brief raportti.md describing exactly what was changed and which test verifies it.
11. The next iteration after this one is automatically a verification iteration. Do not preempt that work in this iteration.

DO NOT:
- Add features
- Refactor unrelated code
- Touch core files (Phase 2A-2/2A-3/2A-4/2A-5 frozen list)
- Modify orchestrator.config.json
- Push to remote
- Open PR
- Create tag or release

SCOPE LIMIT: at most 2 files in the diff. Anything more is an escalation, not a repair.

