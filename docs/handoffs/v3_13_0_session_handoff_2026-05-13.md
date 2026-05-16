# v3.13.0 Sprint 1 Session Handoff (2026-05-13)

**Operator-readable + agent-readable.** If you restart the Claude or
Codex agents (e.g., after travel to the mokki / cottage), read this
file first. It records what shipped, what is open, and how to resume.

**Main HEAD at session close:** `de72326` (post-PR #372). The agents
also shipped a second handoff doc as PR #374
(`955615c`); both are complementary:

* **This doc** -- Sprint 1 narrative: chronological PR table with all
  15 merges, full module/schema/tool surface, all 10 round-2 bugs
  Codex caught, Sprint 2 non-deliverables, 4-option lane menu.
* **`docs/handoffs/2026-05-13-v3.13.0-release-prep-continuity.md`**
  (Codex, in main) -- release-prep continuity: exact postmerge
  evidence with `--basetemp` paths, dedicated-worktree workflow,
  release-gate HOLD reasons.
* **`prompts/2026-05-13-cottage-restart-agent-prompt.md`** (Codex, in
  main) -- ready-to-paste restart prompts for the Codex + Claude
  agents.

Read all three when resuming.

## Yhden minuutin yhteenveto

Sprint 1 v3.13.0 substraatti on mainissa. **15 PR mergetty** mutual
RCO:lla Claudeen ja Codexin valisesti. **0 stop-condition-eskalaatiota.**
**10 round-2-bugia** Codex nappasi Clauden PR:eista (kaikki todellisia
fail-closed-bugeja, ei tyylimuutoksia). 292 v3.13.0-testia menee
lapi. 51 MAGMA-event-tyyppia kaytossa (48 Sprint 1 substrate-landingissa,
+1 BC2 follow-upissa: behavior.capture_refused, +1 SolverProvenance M4
follow-upissa: solver.run_result_recorded, +1 AutoFixLoop AFL5 follow-upissa:
auto_fix_loop.lease_record_unparseable).

**Mitaan ei tarvitse heti tehda.** v3.12.0 stable target (2026-05-24)
ei liiku, v3.13.0 on substrate-only landing -- ei version bumpia, ei
stable-tagia, ei Docker `:latest` -liikutusta. Aktivointi vaatii
Sprint 2 -palasia.

## What landed in this session (chronological)

| Wave | PR | Title | Merge SHA |
|---|---|---|---|
| 1 | #358 | WriteRCOGate v1 | 2d87324 |
| 1 | #359 | Codex Band B inventory generators | 8dc4a1c |
| 1 | #360 | CredentialVault | d5f422f |
| 1 | #361 | ANTI-001..007 catalog | 2e8ab30 |
| 1 | #362 | BehaviorCapture + ShadowRunner | f6328ed |
| 1 | #363 | DivergenceAnalyzer + INST-G09 | fed50b9 |
| 1 | #364 | Codex savepoint PS5.1 ASCII fix | fed8d35 |
| 2 | #365 | SolverProvenance v1 | 92b46c9 |
| 2 | #366 | Codex DEF defaults lock-in | 9a9dfa5 |
| 2 | #369 | Codex SolverProvenance gate/schema wireup | cff15f5 |
| 3 | #367 | sim_orchestrator streaming-mode | 1a2b66d |
| 3 | #368 | AutoFixLoop v1 | a69e925 |
| Polish | #370 | HOME + COTTAGE runbooks + schema regression | 6d2e59b |
| Polish | #371 | Codex ShadowRunner baseline fail-closed | 745dc48 |
| Release | #372 | CHANGELOG + release notes + readiness | de72326 |

Codex round-2 caught these real bugs (each became a regression test):

1. PR #358 WRT-003: state=None could approve; write_modes_allowed not enforced.
2. PR #360: `reveal()` could unwrap silently; URI-parse error echoed raw input.
3. PR #361: ANTI-002 missed uppercase + table-qualified + quoted columns; `re.compile` in function body.
4. PR #362: sensitive_class=secret still passed raw stdin/stdout/stderr to persist_artifact.
5. PR #363: `n_fields_matching` was placeholder zero; `n_fields_compared` only counted diverging fields.
6. PR #365: WRT-003 verified valid with owner+peer only; quarantined still verified valid; sign() let REVOKED re-sign.
7. PR #367: `source.events_path` reported global default; emitted `snapshot_count` off-by-one.
8. PR #368: `acquire_lease()` ignored TTL (no stale takeover); `cursor_advanced` audit broke per-event chain.
9. PR #370: Runbooks published silent-no-op `python -m` commands for non-existent CLIs.
10. PR #372: Release docs claimed 288 tests (actual 292) and 31 events (actual 48).

## Module surface (waggledance/core/v3_13_0/)

| Module | Purpose | Tests |
|---|---|---|
| `write_rco_gate.py` | Single write choke point; 4 risk classes; 12 audit events; 5 stop conditions; WRT-003 calls verify_solver_provenance | tests/v3_13_0/test_write_rco_gate.py (29) |
| `credential_vault.py` | Refused-pickle CredentialMaterial; bound audit emitter; OS keyring + in-memory + no-op | tests/v3_13_0/test_credential_vault.py (38) |
| `anti_pattern_catalog.py` | 7 invariants + 14-pattern credential scanner + no-silent-fail parser | tests/v3_13_0/test_anti_pattern_catalog.py (47) |
| `behavior_capture.py` | OPT-IN capture; sensitive-class retention floors; pipeline linkage | tests/v3_13_0/test_behavior_capture.py (23) |
| `shadow_runner.py` | 6 abort reasons incl. BASELINE_FAILED; clock_fn for tests | tests/v3_13_0/test_shadow_runner.py (9) |
| `divergence_analyzer.py` | 5 format comparators; 7 template severity tables; INST-G09 | tests/v3_13_0/test_divergence_analyzer.py (41) |
| `solver_provenance.py` | Signing chain; auto-quarantine; permanent revoke; sensitive-domain + external_effect operator-sig rule | tests/v3_13_0/test_solver_provenance.py (24) |
| `auto_fix_loop.py` | Event consumer + repair proposer; stale-lease takeover; per-event cursor chain | tests/v3_13_0/test_auto_fix_loop.py (22) |
| `defaults.py` | DEF-001..006 constants + locale noise filters | tests/v3_13_0/test_defaults.py (10) |

Plus `tools/sim_orchestrator.py` gained streaming-mode (`stream()`,
`get_current_metrics()`, `read_events_from_offset()`). 14 tests under
`tests/tools/test_sim_orchestrator_*.py`.

Plus `tools/audit_v3_13_0_event_surface.py` -- machine-derived count
of the 51 MAGMA event types (48 at substrate landing + 1 from BC2
follow-up + 1 from SolverProvenance M4 follow-up + 1 from AutoFixLoop
AFL5 follow-up). Run `python tools/audit_v3_13_0_event_surface.py`
for grouped output; `--count-only` for the integer.

## Schema surface (schemas/v3_13_0/)

`tool_descriptor`, `state_handle`, `authenticated_connector`,
`recovery_capsule`, `mfa_policy`, `profile_config`,
`provider_registry`, `domain_catalog`. New in this session:
`solver_candidate_manifest.schema.json` (SCH-005) with
`provenance_signatures` array + `activation_state` enum
(unactivated, awaiting_signing, signed, activated, quarantined,
revoked).

All schemas validate as JSON-schema draft-7;
`additionalProperties: false` at root + sub-schema level.

## What is NOT in v3.13.0 (Sprint 2+ work)

Explicit non-deliverables to set expectations for the next session:

**Sprint 2 (operator-facing layer):**
1. **DocIngest** -- runbooks reference it; operator must invoke
   parsers manually in v3.13.0.
2. **SolverSynthesizer** -- generates candidate solvers from docs;
   operator constructs manifests by hand in v3.13.0.
3. **SituationRoom** -- external feed + authenticated R/W; SCH-007
   `MfaPolicy` exists as schema but the runtime authenticated reader
   does not.
4. **CLI** for `waggledance.core.v3_13_0.*` modules. There is NO
   `python -m waggledance.core.v3_13_0.shadow_runner` entry point.
   Operator invocation is via direct Python import.
5. **Real-data activation** of any solver. Sprint 1 runs are
   dry-run with synthetic data only.

**v3.14.0+:**
6. **Cryptographic / Yubikey / TPM operator signatures.** v3.13.0
   operator signature is a bridge event with
   `signing_role=operator`; no hardware crypto.
7. **AutoFixLoop autonomous daemon.** v3.13.0 ships
   `run_once(cursor)` operator-driven; long-running daemon harness
   is operator-managed.
8. **Stage-2 cutover.** Specified in
   `docs/architecture/STAGE2_CUTOVER_RFC.md`; NOT executed in
   v3.13.0.
9. **Edge-AI offline mode** for COTTAGE (Sprint 4+); referenced in
   `docs/runbooks/v3_13_0/cottage_dry_run.md` Step 8.

**Sprint 1 polish jaannos (ei estae julkaisua):**
10. Property-based tests (`hypothesis`) for gate fail-closed invariants.
11. Tracing / observability glue for audit envelopes.
12. Solver-RCO end-to-end smoke test across the bridge.
13. Adapter implementations for concrete external systems (bank,
    spot price, weather API).
14. Profile-specific tuning (HOME / COTTAGE / FACTORY default tweaks
    beyond DEF-001..006).

## How to resume after agent restart

### Step 1: Re-sync the working tree

```powershell
cd C:\Python\project2-master   # operator's canonical repo (golden rule #1)
git fetch origin main
git checkout main
git pull --ff-only
```

Expected: `main` at `de72326` or later.

### Step 2: Verify the substrate still passes

```powershell
python -m pytest tests/v3_13_0/ -q
python -m pytest tests/contracts/test_v3_13_schema_bundle.py `
                 tests/contracts/test_v3_13_inventory_generators.py `
                 tests/contracts/test_runbook_examples_v3_13_0.py -q
python -m pytest tests/tools/test_sim_orchestrator_alignment.py `
                 tests/tools/test_sim_orchestrator_stream.py -q
python tools/audit_v3_13_0_event_surface.py --count-only   # expect: 51 (post-AFL5 follow-up; was 48 at substrate landing)
```

Expected: 292 total tests pass (243 + 35 + 14).

### Step 3: Restart the agents

For write-capable agent sessions (Claude Code and Codex when allowed to
edit/commit/push), do NOT launch directly in the shared primary repo at
`C:\Python\project2-master`. Follow the dedicated-worktree bootstrap
documented in:

* `docs/handoffs/2026-05-13-v3.13.0-release-prep-continuity.md`
* `prompts/2026-05-13-cottage-restart-agent-prompt.md`

These contain the exact `New-AgentBridgeWorktree.ps1` + `Start-AgentBridgeSession.ps1`
commands for Codex and Claude. Use one terminal per agent.

Each agent should, after the dedicated-worktree session is started:
1. Read `CLAUDE.md` (operator rules).
2. Read this handoff at `docs/handoffs/v3_13_0_session_handoff_2026-05-13.md`.
3. Read its own memory at `~/.claude/projects/.../memory/MEMORY.md`
   (Claude) or equivalent for Codex.
4. Check the bridge at `.agent-bridge/shared/events.jsonl` for the
   last ~100 lines to recover the conversation context.

### Step 4: Pick the next lane

Coordinator decision (Codex if available; otherwise operator):

* **Option A -- Wait on v3.12.0 release.** v3.12.0 stable target is
  2026-05-24; the R22.5 soak gate is still HOLD with
  `before_no_earlier_than_date`, `soak_window_incomplete`,
  `soak_evidence_missing`. v3.13.0 substrate landing does NOT move
  that target. Nothing urgent here.
* **Option B -- Start Sprint 2 design.** Pick one of DocIngest /
  SolverSynthesizer / SituationRoom. DocIngest is the most upstream
  (runbooks already reference it as Step 2 manual today). For the
  design-doc starting shape, use the committed v3.13.0 release /
  readiness docs (`docs/releases/v3.13.0.md`,
  `docs/release/RELEASE_READINESS.md`) and the corresponding
  `tests/v3_13_0/` module test scaffolding. If local operator-only
  `iterations/anchor_use_case/` artifacts are present, treat them as
  optional reference only, not as restart or source-of-truth material
  (they are local-only per the policy note below).
* **Option C -- Sprint 1 polish backlog.** Items 10-14 above. Each
  is small-scope; can be done by either agent without coordination.
* **Option D -- Real-data activation prep.** Build a HOME or
  COTTAGE pilot with synthetic-but-realistic operator inputs to
  exercise the substrate end-to-end. This catches any integration
  bug between the runtime substrate and operator-facing reality
  before Sprint 2 components land.

If the operator has a preference, that wins. Otherwise the
coordinator should propose a lane split per the
`v3-13-0-release-prep-lane-split-2026-05-13` pattern from this
session.

### Step 5: Honor the mutual-RCO contract

This session's discipline:
* Bridge-RCO every PR (not just GitHub auto-review).
* Round-2 cycles are expected and welcome -- they catch real bugs.
* 30-min RCO target; status-check ping at 30 min if silent;
  takeover offer with explicit option-3 (auto-proceed if silent)
  at 40 min; operator-visible escalation at 75 min for
  high-stakes work like release docs.
* Never merge release-facing docs without independent review,
  even with CI green.
* CLAUDE.md guardrail #9 (autonomous merge after head-match +
  CI green + Codex RCO PASS + no rule violation) holds.

## Quick-reference: files to read first when resuming

1. `docs/handoffs/v3_13_0_session_handoff_2026-05-13.md` (this file)
2. `docs/releases/v3.13.0.md` (release notes; what landed)
3. `CHANGELOG.md` top entry
4. `docs/release/RELEASE_READINESS.md` (v3.13.0 section)
5. `tools/audit_v3_13_0_event_surface.py` (machine-derives the 51
   MAGMA event types post-AFL5; was 48 at substrate landing; run with
   `--count-only` for the integer)
6. Bridge: `.agent-bridge/shared/events.jsonl` last ~100 lines

Note: the wider `iterations/anchor_use_case/` design tree (pattern
catalog, Sprint 0 results, Sprint 1 claude_lane specs) is local-only
by repo policy -- excluded via `.git/info/exclude`, not visible to a
fresh `git ls-tree` even when the directory shows on disk. Resume the
substrate from the committed sources above, not from `iterations/`.

## Bridge state at session close

Last substantive events on the
`v3-13-0-release-surface-addendum-2026-05-13` task (PR #372):

* claude `rco_changes_addressed` at 13:22:38 UTC (round-2 push)
* codex `rco_pass_ci_pending` at 13:25:53 UTC (round-2 PASS)
* (CI green; PR #372 merged at 13:30:46 UTC, main = de72326)

No outstanding bridge requests on the release-surface task after PR #372
and PR #374 merges. The wider bridge has older unresolved requests on
unrelated threads (visible via `Get-AgentBridgeStatus.ps1`); this PR
(#373) also stays open until its own RCO is resolved. Both agents are
at known-good stopping points for the release-surface lane.

## Operator notes

* Mokki trip / agent restart: the substrate is in main and will be
  there when you come back. No state can drift in the absence of an
  active agent because the runtime itself does not run -- it ships
  modules that get invoked by operator code (Sprint 2 components).
* The ground-truth inventory I built mid-session lives at
  `.tmp-claude-1000-obs/v3_13_0_ground_truth_inventory.md` (local,
  not committed; the committed version is the release notes +
  `tools/audit_v3_13_0_event_surface.py`).
* If the bridge events file gets large, the canonical Monitor-
  AgentBridge.ps1 still works. No bridge schema change in v3.13.0.
* Claude memory persisted to
  `~/.claude/projects/C--Python-project2/memory/sprint1_complete_2026-05-13.md`
  (already updated mid-session) and `MEMORY.md` index.
