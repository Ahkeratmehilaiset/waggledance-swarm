# Bridge v2 implementation status and ready queue (F0-F30)

## Current source inventory (2026-09-30; supersedes historical status below)

Installed package reference for this preparation wave:
`7779e9a2145c82389dd97c333bbe3112e3c29ff0`, deployment-manifest SHA256
`8B073EAC5F6232D9FEDC8F68DE5FBFAB2BE58BA6683DBCCEB8FD096863238E4B`.
This is the inherited, hash-pinned Tools generation, not a measurement of
every fleet process. The source preparation head is
`c1400f2065fce292341a2b33479f36de4a3a9437` on
`codex-tools-1/bridge-v2-observability-20260930`; it is **not installed**.
Lead's exact request `2dd5f6a1-1fbb-408e-b74f-c901899bda17` reports its
dashboard checkpoint inspected, committed and pushed. Tools independently
checked that source head and its two-path commit; no remote reachability or
fleet-wide continuity claim follows from this inventory.

| Feature | Present source | Tested evidence | Wired / packaged | Activation / live observation |
|---|---|---|---|---|
| F5 participants | `tools/bridge_lock_participants.py` absent at both exact heads | No F5 test run | Not packaged or wired at these heads | OFF; live coverage unknown |
| F6 dashboard | Absent at installed7779; pure library present at source c1400f20 | Tools' 12 isolated unittest contracts passed before handoff; Lead reports 12 pytest/savepoint passes | Library only, not a CLI; absent from7779 package | Not installed or activated; no live collector/provider observation |
| F25 capacity status | Existing `ops/windows/reboot/Get-WdCapacityStatus.ps1` at7779 | Not rerun for this documentation inventory | Do not duplicate the installed capacity observer; current consumer closure not reverified here | F25 policy OFF; provider headroom unknown |
| F29 doctor | Manifest, Python doctor and PS front end present at7779 | Doctor and PS relay fixture files present; presence alone is not a test pass | `bridge-code-files.json` includes Python file and `bridge_doctor` entrypoint; no direct doctor invocation reference found in either launcher | F29 policy OFF; no live provider/readiness proof |

At7779 the activation config has a null signature and every feature flag
false. Supervisor OFF and merge-driver HOLD remain controls for this wave.
Source preparation is eligible; new installation/activation requires a new
exact operator signature. Existing7779 approvals do not carry forward.
CLI presence, authentication, provider quota and an observed model turn are
independent facts. A process or callback proves neither readiness nor the
next turn. Missing/stale evidence stays unknown. No F0-F30 row below is a
current claim, ownership assignment or proof that work remains missing.

Reproduce this bounded source inventory from the task worktree:

```powershell
git -c safe.directory=C:/Python/project2 rev-parse HEAD
git -c safe.directory=C:/Python/project2 show --stat c1400f2065fce292341a2b33479f36de4a3a9437
git -c safe.directory=C:/Python/project2 ls-tree -r 7779e9a2145c82389dd97c333bbe3112e3c29ff0 -- tools/bridge_lock_participants.py tools/bridge_v2_dashboard.py tools/wd_bridge_doctor.py
rg -n 'wd_bridge_doctor|bridge_doctor' ops/windows/reboot/bridge-code-files.json
rg -n 'Test-WdBridgeComponents|wd_bridge_doctor' ops/windows/reboot/start-wd-agent.ps1 ops/windows/reboot/start-wd-tools-consumer.ps1
```

The final `rg` has no matches at this source head (exit1); it is a narrow
reference check, not proof of complete transitive launch behavior. Test
commands, actual outcomes and log hashes belong to each exact slice reply;
this inventory never substitutes for a frozen-head acceptance review.

## Historical snapshots (retained verbatim below)

Status snapshot of base `8a7576af01e310add445266ed78753a3409f8f7e`, against the map
`docs/architecture/BRIDGE_V2_IMPLEMENTATION_MAP_20260928.md` at
`c099c211a6fd71c109b6349e9e6ccd8794def297`. Written by fable-5 on 2026-09-29 as a
**proposal**: only Lead publishes assignments. Production activation stays OFF.
No test, probe, inventory or model run backs any line here; "present" means
the file exists at the base commit (`git cat-file -e`), not that it works.

## 0. Installed baseline and candidates at `985f7109` (correction, 2026-09-30)

Sections 1-5 are the 2026-09-29 snapshot of the installed base, kept as
history. Their F0-F30 rows, owners, states and queue are historical map
items, not new assignments. This doc assigns and promotes nothing and grants
no WaggleDance core (`waggledance/core/**`) authority. The WD core edits named
in historical rows F8a and F8 are OUT OF SCOPE; tools-owned Bridge queue source
preparation remains within the operator-approved Bridge scope.

- **Installed baseline `8a7576af`**: the only installed source, unchanged.
  Nothing listed after it is installed, activated or restarted.
- **NOT installed candidate: dormant integration `985f7109`**, pushed on
  `codex-lead-1/bridge-v2-integration`. It is source only, 55 paths over the
  baseline (`git diff --name-status 8a7576af 985f7109`), and includes the
  activation files, the log-reader FFI handle-ownership fixes (`ec6d1f4f`,
  `698e8226`), the pool binding (`bae1ac5a`) with its decision-key
  hardening (`985f7109`: collector `0365de0a`, fixture `9a53278f`), the
  resource scope (`043946c2`, `f77dc1ff`, `d6b76e59`), the six pure-port
  paths (`e0041f0c`), Grok helper and controller fixes, and the inventory
  diagnostics source checkpoint `9cde2f8f` (getter `165453d7`, fixtures
  `ecf32274` and `42aa414c`; see `docs/BRIDGE_INBOX_OBSERVATIONS.md`).
  No non-test path passes a pool binder, and a verified binding is pool
  identity only, never quota. Presence in the tree is not activation,
  runtime behavior, readiness or a passing test.
- **NOT installed and UNCOMPOSED**: the Grok seven-path patch returned for
  Lead request `363b347e` (add-only on `e0041f0c`) and the work-queue
  four-path patch returned for `3002209a` (add-only on `ec6d1f4f`). They are
  authorized for source preparation only, are patch text, and are not in
  `985f7109`.

The Bridge v2 fixtures of the candidates are NOT RUN. One disclosed
deviation is kept distinct: the savepoint that composed `d6b76e59` omitted
`-SkipTests` and ran the unrelated WaggleDance file
`tests/test_phase7_hologram_news_wire.py` (20 passed). That run is not
Bridge v2 evidence and backs no line here. Native, security, accounting,
runtime and reboot behavior of the candidates stay UNKNOWN.

## 1. Installed today (base 8a7576af)

**No F0-F30 deliverable is present at the base.** Every new file that the map
names is absent, including `configs/bridge_v2_activation.json`,
`tools/bridge_v2_activation.py`, `tools/wd_switch_policy.py`,
`tools/wd_lane_relaunch_ports_windows.py` and `tools/wd_bridge_doctor.py`, as
are the new `.agent-bridge/bin` helpers, `tests/tools/test_invoke_bridge_git.py`
and the F28 scripts. `configs/model_registry.json` is still
`wd.model-registry.v1` (F3 open). `waggledance/core/work_queue.py` takes no
claim lock (F8a open). `tools/wd_grok_helper.py` does not pass
`--output-format` (F4 open).

Pre-existing building blocks. All 28 inventory files of map §2 are present;
their state is as the map records it, not re-verified:

| Building block | State (map §2) | Feeds |
|---|---|---|
| lane profile catalog, record, binding, launch probe, effective-model resolver | live read-only / dormant | F13, F15, F16, F17 |
| planner `wd_lane_profile_planner.py`, relaunch checks `wd_lane_relaunch.py` | dormant | F15, F16 |
| executor `wd_lane_relaunch_executor.py` (Ports, journal) | dormant, no production ports | F16, F17, F27 |
| recovery store `bridge_capacity_recovery.py` | dormant | F16, F17 |
| pacer, cost meter, collector, attribution, advisor, `Get-WdCapacityStatus.ps1` | dormant / live | F3, F15, F20, F21, F25 |
| event writers (PS + Python), claims, leases, work queue, named mutex, git guard, wake, supervisor | live | F1, F7-F12, F22, F23, F27, F30 |
| Grok hourly helper + read-only session controller (installed 8a7576af, not runtime-tested) | live on request | F4, F20 |

In flight, not in the base (static review only, not installed): the Grok
controller corrections on `fable-5/grok-controller-corrections-20260929` at
`eddd3f53`, plus the Tools helper `timeout_seconds` change. These are not
Bridge v2 slices.

## 2. Draft and unimplemented slices

Owner letters follow the map (L Lead, T Tools, F Fable) before today's
re-assignment. Class: **plain** = `tools/**` new files and docs; **signed** =
touches `ops/**`, `.agent-bridge/**`, `waggledance/core/**` or `CLAUDE.md`,
so it needs a per-PR operator signature.

| Slice | Map owner | Files (map §4) | Class | Depends on | State |
|---|---|---|---|---|---|
| F0 activation + kill switch | L | new `configs/bridge_v2_activation.json`, `tools/bridge_v2_activation.py` | plain | none | assigned RCO1 by Lead |
| Contract F15/F16/F17 | L+F | `docs/architecture/BRIDGE_V2_SWITCH_INTERFACE_CONTRACT.md` | plain | none | drafted with this doc |
| F1 wake telemetry | L | mod `BridgeTelemetry.ps1`; new `tools/bridge_wake_telemetry.py` | signed | none | open |
| F2 role contract in repo | L | new `.agent-bridge/contracts/`; mod `start-wd-agent.ps1`, `start-wd-tools-consumer.ps1`, `Deploy-WdRebootBundle.ps1` (3 places); new lint | signed | none | open |
| F3 model registry v2 | T | mod registry, `wd_model_registry.py`, collector, cost meter | plain* | none | open |
| F4 Grok helper JSON + ledger | T | mod `tools/wd_grok_helper.py` | plain | the helper timeout change landing | open |
| F5 lock participants | T | new `tools/bridge_lock_participants.py` | plain | none | open |
| F6 dashboard | T | new `tools/bridge_v2_dashboard.py` | plain | F0 | open |
| F7 wake backpressure | L | mod `Watch-Bridge.ps1`, Tools consumer relay, `start-wd-agent.ps1`; new `Drain-BridgeWake.ps1` | signed | F0, F1 | open |
| F8a work-queue claim lock | F | mod `waggledance/core/work_queue.py` | signed | none | open (proposed 09-28) |
| F8 queue mutex + CAS + outbox | F | mod `work_queue.py`, `Claim-AgentTask.ps1`, `Release-AgentTask.ps1`; new `Publish-BridgeOutbox.ps1` | signed | F8a | open |
| F9 git guard | F | mod `Invoke-BridgeGit.ps1`; new `tests/tools/test_invoke_bridge_git.py` | signed | none | open |
| F10 lease owner check | F | mod `ClaimLeaseHeartbeat.ps1`, `Start-BridgeHeartbeat.ps1`, owner check | signed | F8 lock order | open |
| F11 Reply-ToRequest | F | new `.agent-bridge/bin/Reply-ToRequest.ps1` | signed | none | open |
| F12 head check parity | F | mod `Write-AgentEvent.ps1`, `bridge_event_schema.py` | signed | none | open |
| F13 explicit launch | L | mod `wd-fleet.json`, `start-wd-agent.ps1`, `start-wd-tools-consumer.ps1` | signed | F3 | open |
| F15 switch policy | F | new `tools/wd_switch_policy.py` (pure) | plain | contract; F0 read via injection | open, next Fable coding slice |
| F16 production ports + runner | L | new `tools/wd_lane_relaunch_ports_windows.py`, `Invoke-WdSwitchExecutor.ps1`; mod executor gate | signed (runner) | F0, F15, F17 | open |
| F17 journal machines + fenced CAS | F | mod `wd_lane_relaunch_executor.py`, `AgentBridgeSessionIdentity.ps1` | signed (PS part) | F8 lock | open |
| F18 wd-model CLI | F | new `ops/windows/reboot/wd-model.ps1`, `wd-malli.cmd` | signed | F16 intents | open |
| F19 task router + ledger | F | mod `bridge_work_ledger.py`; new `tools/wd_task_router.py`; mod `CLAUDE.md` Rule 8 | (a)-class for CLAUDE.md | F3, F24 | open |
| F20 Grok consult role + broker | T | mod `bridge_workflow.py`, advisor; new `tools/wd_grok_broker.py` | signed | F4 | open |
| F21 qualification harness | F writes, T runs | new `tools/wd_profile_qualification.py`, `tests/fixtures/qualification/` | plain | F3 | open |
| F22 scope explain | F | mod `BridgeResourceScope.ps1`, `Claim-AgentTask.ps1` help | signed | F8 (same file) | open |
| F23 reserved labels | T reproduces, F fixes | mod `Write-AgentEvent.ps1`, `bridge_event_writer.py`, sweep, probe | signed | F12 (same file), F8 outbox | open |
| F24 composer select | F | new `tools/wd_composer_select.py` (pure) | plain | F3 | open |
| F25 capacity status per pool | T | mod `Get-WdCapacityStatus.ps1`, `start-wd-agent.ps1` line, advisor | signed | F3 | open |
| F26 routing weights (shadow) | F writes, T runs | new `tools/wd_routing_weights.py` | plain | F0, F19 | open |
| F27 stand-in + task journal | L | new `tools/wd_task_journal.py`; executor stand-in path | plain + executor | F16, F17 | open |
| F28 release worktree + rollback | L | new `New-WdReleaseWorktree.ps1`, `Restore-WdRebootBundle.ps1` | signed (a)-class | after stages 1-5 | open |
| F29 bridge doctor | T implements, F tests | new `configs/bridge_components.json`, `tools/wd_bridge_doctor.py`, `Test-WdBridgeComponents.ps1`, `Initialize-WdBridge.ps1`; mod `Start-AgentBridgeSession.ps1`, `start-wd-agent.ps1`, `Deploy-WdRebootBundle.ps1` | signed | none for the new files | assigned RCO2 by Lead |
| F30 wake content | L implements, F tests | mod Tools consumer, `BridgeTelemetry.ps1`, `Watch-Bridge.ps1`; new `Get-BridgeEvent.ps1`; tests | signed | F1, F2, F7 | open |

\* F3 edits `configs/**` and `tools/**` only; confirm its class against the
charter before merging.

## 3. File conflicts the queue must serialize

These pairs touch the same file, so one owner or a strict order is required:

- `start-wd-agent.ps1`: F2, F7, F13, F25 **and F29**. Proposal: split F29 into
  **F29a** (the new files only: manifest, doctor, PS front end, initializer)
  and **F29b** (the preflight wiring), and give F29b to the Lead-owned
  launcher sequence F2 → F13 → F29b → F25.
- `Deploy-WdRebootBundle.ps1`: F2 and F29b. Same Lead sequence.
- `Write-AgentEvent.ps1`: F12 then F23.
- `Claim-AgentTask.ps1`: F8 then F22.
- `wd_lane_relaunch_executor.py`: F17 (journal) then F16 (mode gate), then F27.
- `AgentBridgeSessionIdentity.ps1`: F10 and F17. One owner, F10 first.
- `BridgeTelemetry.ps1`, `Watch-Bridge.ps1`, the Tools consumer: F1 → F7 → F30 (Lead).
- `bridge_capacity_advisor.py`: F20 and F25 (Tools, one at a time).
- `tools/wd_grok_helper.py`: the current timeout change, then F4.

## 4. Proposed ready queue (Lead decides)

Heavy coding goes to RCO1, RCO2 and Fable; Tools gives limited support; Lead
integrates into `codex-lead-1/bridge-v2-integration`. Each package is
file-disjoint from the others running at the same time.

| Lane | Now (assigned) | Next | Then |
|---|---|---|---|
| RCO1 | F0 | F8a → F8 (queue lock, CAS, outbox) | F10, F22 |
| RCO2 | F29a doctor (new files) | F9 git guard + first pytest suite | F12 → F23 (after Tools' reproduction) |
| Fable | contract (this doc) | **F15** pure switch policy | F17 → F24 → F11 → F26 (shadow) → F18 |
| Tools (limited) | the helper timeout change | F3 → F4 → F21 runs | F5, F6, F25 → F20; runs tests for F-slices on request |
| Lead | integration branch | F1 → F2 → F13 → F29b | F7 → F30, F16 → F27, F28 |

Review constraint: a slice authored by RCO1 or RCO2 cannot be passed by its
author. The other RCO must pass it, so the dual-RCO standing sign
(CLAUDE.md 9b) is unavailable for RCO-authored PRs. Those need the single
independent RCO pass plus an explicit operator signature where the class
is signed.

## 5. Limits

- Built from the map plus file-existence checks at the base. Behavior is
  not verified, and map §2 states are carried over, not re-measured.
- No assumption about quota pools, account binding or model identity is
  made. Capacity-based lane choice stays with Lead at dispatch time.
- Ordering inside a stage is a proposal. Dependencies follow map §4 and §5
  and the file conflicts in §3.
