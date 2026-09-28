# Bridge v2 implementation map: code level, release and production

Status: **design companion to `BRIDGE_NEXT_WORK_PLAN_20260928.md`** (same
PR #1753). It maps every plan feature (F1-F27, plus F28 below) to today's code
on `main` c5f7c933, and describes one path from the code to a release and to
production under one operator signature. It grants no authority: no merge,
deploy, activation, signature or assignment.

Sources:
- three read-only code surveys of `main` c5f7c933 (model/profile/Grok stack,
  bridge core, release and deploy path), 2026-09-28;
- fable-5's own verification of the facts in §1.

`file:line` references are to `main` c5f7c933 and move with later commits.
They are regenerated from that exact commit before any slice starts; READ
facts about the code are kept apart from runtime outcomes, which need a
reproduction.
Labels: **READ** = confirmed by reading the code; **UNVERIFIED** = not
checked, listed in §9.

## 1. Corrections to the plan from reading the code

These change the plan's §1 statements and some slice sizes.

| # | Fact (READ) | Where | Effect on the plan |
|---|---|---|---|
| C1 | *(corrected after Lead's review)* The PowerShell writer does check identity: `agent_uuid` is checked against the identity registry and the agent profile (`Assert-AgentUuidMatchesIdentityRegistry` :508 and `Assert-AgentUuidMatchesProfile` :476, both called at :549-550). What it lacks is the **reserved-label and session-origin** enforcement that `Assert-AgentBridgeSessionIdentity` (:47, refusal at :68-72) gives the claim, release, heartbeat, liveness and session scripts. Whether an unbound `agent=operator` event succeeds therefore depends on the registry and profile checks, and has not been reproduced. The stale sweep writes as `system` (`Invoke-StaleClaimSweep.ps1:276`). | `Write-AgentEvent.ps1:476,508,549-550`; `AgentBridgeSessionIdentity.ps1:47,68-72` | F23 first reproduces, in isolation, both writers with the registry and profile present, missing and mismatched; then it fixes only what reproduces. |
| C2 | There is no `WorkQueueV1` mutex and no `work_queue_busy` anywhere. The Python work queue takes no lock: `heartbeat` and `release_task` are unlocked, and `_write_json_file` (:900) finishes with `tmp.replace(path)` (:920), which by reading could recreate a claim archived meanwhile; that runtime outcome is **not yet reproduced**. The PowerShell claim scope-conflict scan is also unlocked. | `waggledance/core/work_queue.py:339-403,406-461,900,920`; `Claim-AgentTask.ps1:111-148` | F8 is a new lock plus a CAS rewrite, larger than "wire the #1751 helper"; the race is reproduced before a fix is claimed. |
| C3 | The git guard takes the verb from `$GitArgs[0]` and parses no leading option. `GIT_CONFIG_*` is not checked. `-Force` trusts a self-asserted `operator`/`system` label. There are no tests under `tests/**`; the only coverage is a smoke script outside CI. | `Invoke-BridgeGit.ps1:88-108,305-307,382` | F9 also covers `-Force` identity and adds the first pytest suite. |
| C4 | `Assert-AgentBridgeClaimOwner` is defined but never called. | `AgentBridgeSessionIdentity.ps1:215` | F10 wires it into claim force, release and heartbeat. |
| C5 | Role prompts live outside the repo (`C:\Python\wd-agent-prompts\*.md`, from `wd-fleet.json` `lanes[].prompt`) and are not hashed; the launcher only checks that they are non-empty. | `start-wd-agent.ps1:2000,2225,2473` | F2 must bring the role contract into the repo and the bundle manifest. |
| C6 | The supervisor never starts or stops interactive lanes (`start-wd-all.ps1 -Auto` does). A new bundle does not restart live lanes; the rollover order is manual. | `wd_supervisor.ps1:1-17`; `start-wd-all.ps1:3779` (the launch; :3396-3403 only prints the plan) | F16/F27 need a lane start/stop port that does not exist today; the §7 rollout keeps the manual order. |
| C7 | The model registry has no Grok row and no pool fields, and its validator forbids pool keys. | `configs/model_registry.json`; `wd_model_registry.py:58-62` | F3 is a schema bump to `wd.model-registry.v2`. |
| C8 | No cryptographic operator signature exists. The operator signature is a bound instruction text (`wd.operator-signed-merge.v1` audit). | `docs/operations/BRIDGE_FINAL_ACCEPTANCE_20260927.md:72-94` | §6 uses that form. |
| C9 | There is no bundle-rollback tool. The installer restores its backup only when an exception is thrown, and that restore is not crash-atomic. | `Deploy-WdRebootBundle.ps1:1151-1181` | New slice F28 (§5). |
| C10 | CI is Linux only (6 check runs). There is no Windows or PowerShell runner. | `.github/workflows/ci.yml`, `tests.yml` | Windows and PowerShell evidence stays the isolated fable-5 matrix. |
| C11 | Commit c593b243 (claims bound to session generation) is not on `main`. | `git merge-base --is-ancestor` | Nothing in this plan depends on it. |
| C12 | There is no general feature-flag system, only environment kill switches and a few JSON switches. | `Start-AgentBridgeSession.ps1:224-269`; `wd_supervisor_loop.json:45` | New signed activation config (§5, F0). |

## 2. What exists today (inventory)

**Live** means production launchers, scheduled tasks or the supervisor call
it. **Shadow** means it computes decisions but never acts on them.
**Dormant** means only tests or manual runs call it.

| Component | File (main) | Status | Plan feature |
|---|---|---|---|
| Model registry + value analysis | `configs/model_registry.json`; `tools/wd_model_registry.py` (`validate_registry` :132, `frontier` :233, `lane_value_table` :310) | dormant | F3, F24 |
| Profile catalog (13 profiles, all `approved:false`, `UNSIGNED-DEFAULT`, `mode:shadow`) | `configs/lane_profile_catalog.json`; `tools/lane_profile_catalog.py` (`is_signed` :196, `effective_mode` :314, `classify_transition` :351) | live read-only | envelope, F15 |
| Per-lane profile record | `tools/lane_profile_record.py` (`launch_decision` :217, `write_record` :193 has no production caller) | live read-only | F16 |
| Session-to-lane binding | `tools/lane_profile_binding.py` (`bind_lane` :56) | dormant | F16, F17 |
| Launch probe + preflight (`alert_only`) | `tools/lane_profile_launch_probe.py` (`preflight` :98); `start-wd-agent.ps1:1304-1392,2732`; `start-wd-tools-consumer.ps1:355,2410` | live, alert only | F13 |
| Effective-model resolver | `tools/lane_effective_model.py` (`resolve_claude` :492, `resolve_codex` :639, `classify` :698) | live via the probe | F13, D3 verification |
| Lanes set to `native` | `ops/windows/reboot/wd-fleet.json:114-115,157-158,184-185,210-211,236-237` | live | F13 |
| Planner | `tools/wd_lane_profile_planner.py` (`plan_lane` :69, `execution_allowed: False`) | dormant | F15 |
| Relaunch checks | `tools/wd_lane_relaunch.py` (`check_request` :36, `check_safe_boundary` :123) | dormant | F15, F16 |
| Executor (ports, journal) | `tools/wd_lane_relaunch_executor.py` (`Ports` :73-92, `Executor` :134, mode gate :430-444, journal :498-575) | dormant, **no production ports** | F16, F17, F27 |
| Recovery store | `tools/bridge_capacity_recovery.py` (`RecoveryStore` :127) | dormant | F16 journal |
| Pacer (forecast to reset) | `tools/wd_capacity_pacing.py` (`pace_windows` :130, `recommend` :193; Grok always `capacity_unobserved` :258-264) | dormant | R13 forecast, F15 |
| Cost meter | `tools/wd_profile_cost_meter.py` (`profile_rollup` :369, `report` :524) | dormant | F3, F21 |
| Capacity collector | `tools/bridge_capacity_collector.py` (`account_pool=None` at :300, :313, :615) | live (`WD-CapacityObserver`, hooks) | F3, F25 |
| Attribution | `tools/bridge_capacity_attribution.py` (`attribute` :475) | live, opt-in | F3 |
| Advisor | `tools/bridge_capacity_advisor.py` (Grok refusal :344-345) | live as a library | F20 |
| Status reader | `ops/windows/reboot/Get-WdCapacityStatus.ps1` (`quota_pool_binding='unverified'` :147) | live | F25 |
| Grok hourly helper | `tools/wd_grok_helper.py` (`consult` :113, reservation :154-168, stderr dropped :192) | live via `Invoke-WdGrok.ps1` | F4, F20 |
| Grok routing refusal | `waggledance/core/bridge_workflow.py:38-39` | live | F20 |
| Event writer (PowerShell) | `.agent-bridge/bin/Write-AgentEvent.ps1` (envelope :585-603, reply binding :605-680, rco_pass :421-470) | live | F11, F12, F23 |
| Event writer (Python) + schema | `tools/bridge_event_writer.py`; `waggledance/core/bridge_event_schema.py:383` | live | F12, F23 parity |
| Claims | `Claim-AgentTask.ps1` (record :214-262, scan :111-148); `Release-AgentTask.ps1:55-163`; `BridgeResourceScope.ps1:24-33` | live | F8, F10, F22 |
| Leases, heartbeat | `ClaimLeaseHeartbeat.ps1` (`Update-BridgeClaimLease` :313); `Start-BridgeHeartbeat.ps1:101-153` | live | F10 |
| Work queue (Python) | `waggledance/core/work_queue.py`; `tools/work_queue_sweep_stale.py` | live | F8 |
| Named mutex helper | `tools/bridge_named_mutex.py` (`create_bridge_named_mutex` :230) | live (append, publication, spool) | F8 |
| Git guard | `.agent-bridge/bin/Invoke-BridgeGit.ps1` | live | F9 |
| Wake | `Watch-Bridge.ps1:119-181` (byte cursor, sentinel); `BridgeTelemetry.ps1:28`; Codex relay `start-wd-tools-consumer.ps1:471-594` | live | F1, F7 |
| Supervisor | `ops/windows/reboot/wd_supervisor.ps1` (watchers, Tools consumer, task containment; `Stop-VerifiedProcessTree` :2334) | live | F16, F27 |
| Installer | `ops/windows/reboot/Deploy-WdRebootBundle.ps1` (detached-HEAD refusal :597-617, DryRun :832, StageOnly :967, activation :979-1149) | manual | §7 |
| Package list | `ops/windows/reboot/bridge-code-files.json` (26 Python files, 8 entrypoints, 5 pinned wheels) | build input | every new Python tool |

## 3. Signature class of the touched paths

The charter (`docs/architecture/IDLE_AUTONOMY_CHARTER.md:37-137`, loader
`waggledance/core/idle_consensus_charter.py`) and
`check_standing_consensus_sign_class.py:111-123` put almost every touched
path outside autonomous merge:

| Path | Class |
|---|---|
| `.agent-bridge/bin/**`, `CLAUDE.md`, `tools/bridge_event_writer.py`, gate tools | charter denylist: operator-explicit |
| `ops/windows/reboot/**` | not on the allowlist; (a)-class: operator-explicit |
| `waggledance/core/bridge_workflow.py`, `work_queue.py`, `bridge_event_schema.py` | not on the allowlist: operator signature |
| `configs/*.json` | not on the allowlist: operator signature |
| `tools/**` (other), `tests/**`, `docs/architecture/**`, `docs/operations/**` | allowlisted, but ride with the rest in one composed PR |

**Consequence:** like #1751, the package can land only through the manual
operator-signed merge path. That fits "one signature": one composed
integration PR, one signed packet (§6).

## 4. Slice map: every feature to code

Notation:
- **mod** = modify an existing file; **new** = new file.
- Tests are always new or extended under `tests/**`, and run in both
  PowerShell 5.1 and 7 wherever PowerShell is touched.
- One owner per physical file (plan §3). Where two features touch one file,
  the first owner listed owns it.

### Stage 0: shared foundations (new, first)

- **F0 Activation config and kill switch** (new; owner L).
  - `configs/bridge_v2_activation.json`: schema `wd.bridge-v2-activation.v1`
    with the per-feature flags, stage predicates, the absolute expiry, the
    signed parameters (plan §4) and the policy bits.
  - `tools/bridge_v2_activation.py`: loader and validator, and the
    `feature_enabled(name)` function that every new component calls.
  - The reviewed activation policy is immutable once signed. On top of it, a
    **durable, versioned revocation and freeze state** under the runtime
    root is checked at every dispatch and before every side effect. A
    changed environment variable does not reach processes that are already
    running, so `WAGGLE_BRIDGE_V2_ENABLED=0` is only an additional local
    deny, never the fleet-wide switch.
  - A missing, corrupt or stale policy or revocation state means disabled.
    Dependencies between optional bits are validated.
  - Runtime stage state (`<runtime>/bridge_v2/stage_state.json`) records
    progress only; it can never grant a flag the signed policy does not
    grant.
  - Default: every flag off.
- **Interface contract F15/F16/F17** (new doc; L+F):
  `docs/architecture/BRIDGE_V2_SWITCH_INTERFACE_CONTRACT.md`. It defines the
  intent schema `wd.switch-intent.v1`, the journal states and the port
  signatures, and maps **every** existing executor journal phase and
  transition reason (`planned` → REQUESTED, `quiesced` → QUIESCED,
  `checkpointed`, `apply_pending` → FENCED/APPLIED, `verified` → VERIFIED,
  `resume_pending` → VERIFIED awaiting continuity, `resumed` → CONTINUED,
  `cancelled_before_apply` → a terminal cancel). Two cases need care:
  - a failed resume is left at `resume_pending` (`wd_lane_relaunch_executor.py:564-572`)
    and must never be read as CONTINUED;
  - a rollback also lands in `verified` with the reason
    `rolled_back_to_previous` (:559), so VERIFIED is derived from the phase
    **and** the reason, and a rollback never counts as a successful switch.

### Stage 1: measurement, contracts, visibility

| F | Owner | Code change |
|---|---|---|
| F1 | L | **mod** `BridgeTelemetry.ps1` (`Write-BridgeWakeObservation` :28: add reason, watermark and latency); **new** `tools/bridge_wake_telemetry.py` (read-only report: no-op ratio, latency). |
| F2 | L | **new** `.agent-bridge/contracts/role-contract.v1.md` (moves the role prompt content into the repo); **mod** `start-wd-agent.ps1` (:2000, :2225, :2473: verify the contract SHA-256 from the bundle manifest), `start-wd-tools-consumer.ps1`, `Deploy-WdRebootBundle.ps1` (three places, not one: the `git archive` pathspec at :635-640 must include `.agent-bridge/contracts`, the non-recursive bin copy at :675-678 must copy it, and only then the required-file list :748-793; a required name that never entered `$sourceHashes` throws at :790-791. The same applies to every new bundle file of F0, F3 and F29); **new** `tools/lint_role_contracts.py` (CI lint). Canary one lane; the previous hash stays valid. |
| F3 | T | **mod** `configs/model_registry.json` → schema `wd.model-registry.v2` (pools, `limit_id`, tier, context, quality per class, `source_measured_at`, and Grok and Haiku rows; the Grok 4.7 row with its high and xhigh values is specified in plan §2.1); **mod** `tools/wd_model_registry.py` (:58-62 key sets, `validate_registry` :132); **mod** `bridge_capacity_collector.py` (`account_pool` from validated provenance, replacing the `None` at :300/:313; unknown stays unknown); **mod** `wd_profile_cost_meter.py` (stored daily points per Mtok; schema-failing rows counted as unknown residual). |
| F4 | T | **mod** `tools/wd_grok_helper.py` (`consult` :113: `--output-format json`, keep stderr at :192, record model, effort, tokens and error class); **new** ledger `C:\Python\grok-scout-reports\ledger.jsonl` schema `wd.grok-ledger.v1`, including calibration runs. |
| F5 | T | **new** `tools/bridge_lock_participants.py`. It enumerates participant processes (logon, integrity) and keeps them apart from *proven* mutex handle holders: reading the ACL and the lane tokens does not establish every creator or opener. Missing coverage stays unknown; more than one logon or integrity level means HOLD. |
| F6 | T | **new** `tools/bridge_v2_dashboard.py` (read-only: pools, lanes, intents, stages). |
| F21 | F writes, T runs | **new** `tools/wd_profile_qualification.py` + `tests/fixtures/qualification/` (synthetic and replayed tasks per class, adversarial holdouts); receipts `wd.profile-receipt.v1` bound to code SHA, profile, provider version and freshness; isolated worktrees, no production writes. |
| F30 | L implements, F tests | **mod** `ops/windows/reboot/start-wd-tools-consumer.ps1` (`Invoke-WdNativeToolsWakeStep` :503; replace the fixed messages at :542-564 with a header plus one validated line per binding, read from the `wd.bridge-wake-observation.v1` snapshot already parsed at :575-586, moved before the send); **mod** `.agent-bridge/bin/BridgeTelemetry.ps1` (`Write-BridgeWakeObservation` :28: add task id, event type/status, `ts_utc`, the canonical event SHA-256 and the wake class to each binding; classify by type and status only); **mod** `Watch-Bridge.ps1` (informational events go to a digest, not a wake); **new** `.agent-bridge/bin/Get-BridgeEvent.ps1` (exact fetch by request id plus the expected hash); **mod** the F2 role contract (takes the standing rules now in the wake text); **mod** `tests/tools/test_wd_native_tools_wake.py` (:75 asserts `TRUNCATED ROUTING SUMMARY`) and `tests/tools/test_wd_lead_reply_delivery.py` (:38, :41, :140), plus every consumer that `git grep` finds for the wake strings. Depends on F1, F2 and F7. |
| F29 | T implements, F tests | **new** `configs/bridge_components.json` (`wd.bridge-components.v1`); **new** `tools/wd_bridge_doctor.py` + `.agent-bridge/bin/Test-WdBridgeComponents.ps1` (one manifest, two front ends); **new** `ops/windows/reboot/Initialize-WdBridge.ps1` (first-run local config from templates, never overwriting); **mod** `Start-AgentBridgeSession.ps1`, `start-wd-agent.ps1`, `Deploy-WdRebootBundle.ps1` (call the doctor in preflight); replace hard-coded machine paths in new code with config values (existing examples: `wd_grok_helper.py:17` `STATE_ROOT`, the installer's `$BundleStore` default at `Deploy-WdRebootBundle.ps1:17-28`, the writer's hard-coded fleet targets at `Write-AgentEvent.ps1:668`). |
| F25 | T | **mod** `Get-WdCapacityStatus.ps1` (:38, :147: every pool with age and source); boot brief line in `start-wd-agent.ps1`; **mod** `bridge_capacity_advisor.py` for a peer-pool view. |

### Stage 2: wake backpressure

| F | Owner | Code change |
|---|---|---|
| F7 | L | **mod** `Watch-Bridge.ps1` (:119-181: durable watermark plus a dirty flag, alongside the byte cursor); **mod** `start-wd-tools-consumer.ps1` relay (`Invoke-WdNativeToolsWakeStep` :503, `Send-WdNativeToolsQueueMessage` :471: one outstanding typed notification per lane, reconcile instead of resubmit); **mod** `start-wd-agent.ps1:253-281` (Lead imports); **new** `.agent-bridge/bin/Drain-BridgeWake.ps1` (pinned drain helper, watermark authority); vetoes, cancels and late replies bypass coalescing; backlog migration and a rollback to the relay under the F0 flag. |

### Stage 3: queue and guard correctness

| F | Owner | Code change |
|---|---|---|
| F8 | F | **new** work-queue mutex whose name is derived from the normalized canonical runtime-root identity (never one global constant shared by production and tests), created through `bridge_named_mutex.create_bridge_named_mutex` :230 (Python) and the PowerShell equivalent. A mutex plus two file writes is not crash-atomic, so the slice specifies the WAL and outbox recovery for every crash cut point, idempotent event publication, and no rollback to unsafe older writers while mixed generations run; **mod** `waggledance/core/work_queue.py` (lock around claim, release :339, heartbeat :406 and the sweep :493-621; replace the final `tmp.replace(path)` of `_write_json_file` (:900, the replace at :920) with a compare-and-swap that refuses to recreate archived claims; outbox record in the same critical section); **mod** `Claim-AgentTask.ps1` (:111-148 scan **and** the `CreateNew` create at :269-276 under the lock), `Release-AgentTask.ps1`. An existing per-claim lock is live: `Enter-BridgeClaimLock` (`ClaimLeaseHeartbeat.ps1:229`, a sibling `<claim>.lock` opened with `FileShare.None`), held by the sweep (`Invoke-StaleClaimSweep.ps1:133`), the heartbeat (`ClaimLeaseHeartbeat.ps1:337`, :431, :504), release (`Release-AgentTask.ps1:64`) and claim refresh (`Claim-AgentTask.ps1:289`). The Python work queue never takes it. The slice defines one lock order (the runtime-root mutex first, then the per-claim lock), and Python takes the same `<claim>.lock` with share mode none, so a Python `tmp.replace` cannot recreate a claim that a PowerShell writer archived; **new** `.agent-bridge/bin/Publish-BridgeOutbox.ps1`. |
| F9 | F | **mod** `Invoke-BridgeGit.ps1` (:88-108, :305-307: parse leading options; `-C` guarded against its target; `-c` allowlist; refuse `--git-dir`, `--work-tree`, `--namespace` and the `GIT_CONFIG_*`, `GIT_DIR`, `GIT_WORK_TREE`, `GIT_NAMESPACE` variables on branch moves; `-Force` at :382 calls `Assert-AgentBridgeSessionIdentity`); **new** `tests/tools/test_invoke_bridge_git.py` (first pytest suite, with success twins). |
| F10 | F | **mod** `ClaimLeaseHeartbeat.ps1` (`Update-BridgeClaimLease` :313: follow the long-lived worker, per-task retirement, progress proof → `wedged`); **mod** `Start-BridgeHeartbeat.ps1`; wire an owner check into force, release and heartbeat. **Not `Assert-AgentBridgeClaimOwner` as it is:** its test (`Test-AgentBridgeClaimOwner`, `AgentBridgeSessionIdentity.ps1:190-212`) requires `owner_pid` and `owner_process_start_utc`, which Python claims never write (`work_queue.py:879-881`) and the PowerShell writer records only when available (`Claim-AgentTask.ps1:245-248`, informational). The live authority is session plus token hash (`ClaimLeaseHeartbeat.ps1:300-310`). The slice aligns the two tests on that authority (the pid fields stay informational), with tests on Python-written and PowerShell-written claims. |
| F11 | F | **new** `.agent-bridge/bin/Reply-ToRequest.ps1` (fetches the request by id through `BridgeReplyIndex.ps1:82`, then calls `Write-BridgeTaskReply.ps1`); requester supersede = a new id plus a non-coalesced cancel. |
| F12 | F | **mod** `Write-AgentEvent.ps1` (extend the 40-hex head check at :421-470 to `build_consensus_pass` and the other commit statuses); **mod** `bridge_event_schema.py` for parity. |
| F22 | F | **mod** `BridgeResourceScope.ps1` (:24-33: `-Explain`, examples in the error text); **mod** `Claim-AgentTask.ps1` help. |
| F23 | T reproduces, F fixes | First reproduce, isolated from production, both writers with the registry and profile present, missing and mismatched (C1), and the wrapper-attribution suspicion. Then **mod** `Write-AgentEvent.ps1` and `tools/bridge_event_writer.py` so reserved labels need session-origin enforcement. The sweep's internal path is never a caller-supplied `-Internal` switch or a `system` label: it is limited to bounded event kinds and fields, from a trusted entrypoint with session provenance. Every current reserved-label caller is enumerated and migrated in the same slice: the stale sweep (`Invoke-StaleClaimSweep.ps1:276-277`, `-Agent system`) and the responsiveness probe (`ops/windows/reboot/Test-WdBridgeResponsiveness.ps1:107-108`, `-Agent operator -Role operator` with a probe id as its session). The sweep catches a writer failure and continues (:285-287), so enforcement alone would make its release events vanish while the archive still happens: the sweep must fail visibly, or write through the outbox (F8). The shared-account trust limits stay disclosed. |

### Stage 4: explicit launch

| F | Owner | Code change |
|---|---|---|
| F13 | L | **mod** `wd-fleet.json` (replace `native` at the five lines in §2 with explicit catalog profiles); **mod** `start-wd-agent.ps1:1304-1392` and `start-wd-tools-consumer.ps1:355` (preflight `alert_only` → `enforce`: refuse a mismatch; rollback to the previous qualified profile or the signed safe default); signed catalog (`operator_signature` replaces `UNSIGNED-DEFAULT`); **mod** `start-wd-agent.ps1` to normalize `PSModulePath` for Windows PowerShell at the top, as `start-wd-all.ps1:59-72` does. Today it has no such step but calls `Get-FileHash` at :1426 and eleven more places, so a direct launch from a pwsh 7 parent fails (the #1751 RCO1 relaunch, 06:46Z), with a PS5-under-pwsh7 regression test. |

### Stage 5: policy, actuation, continuity, learning

| F | Owner | Code change |
|---|---|---|
| F15 | F | **new** `tools/wd_switch_policy.py` (pure; uses `wd_capacity_pacing.pace_windows`/`recommend`, `wd_lane_profile_planner.plan_lane`, `lane_profile_catalog.classify_transition`; every §2.2 guardrail, atomic pool admission, deterministic contest rule, member precedence). |
| F16 | L | **new** `tools/wd_lane_relaunch_ports_windows.py` (production `Ports` for `wd_lane_relaunch_executor.py:73-92`: `take_claim`/`release_claim` → Claim/Release scripts; `stop` → a **new adapter** around `Stop-VerifiedProcessTree` (`wd_supervisor.ps1:2334`) under a job object. The adapter is needed because that function takes a root process, the initial tree and a Tools conflict path, and it throws Tools replacement conflicts (`Throw-ToolsReplacementConflict`, :2369 and later) instead of returning the `bool` that `Ports.stop` (`wd_lane_relaunch_executor.py:86`) requires. The executor calls `stop` without a catch (:516), so the adapter maps every throw to `False` with the reason recorded. The PID and start-time identity check compares values from **one source** (CIM against CIM): the #1751 rollout stopped at 06:33Z on a sub-microsecond CIM versus `Get-Process` difference; `launch` → `start-wd-agent.ps1`; `emit` → `Write-AgentEvent.ps1`; `verify_catalog_signature` → the packet hash); **new** `ops/windows/reboot/Invoke-WdSwitchExecutor.ps1` (serialized, supervisor-run, intent queue `<runtime>/bridge_v2/intents/`); **mod** the executor mode gate :430-444 to read F0. |
| F17 | F | **mod** `wd_lane_relaunch_executor.py` journal (two machines; `identity_changed` notices; fenced CAS through the F8 lock); **mod** `AgentBridgeSessionIdentity.ps1` (identity re-presentation only for a qualified resume). |
| F18 | F | **new** `ops/windows/reboot/wd-model.ps1` + `wd-malli.cmd` (status, models, set, reset, freeze; enqueue only; one tier table from the catalog; printed default expiry). |
| F19 | F | **mod** `tools/bridge_work_ledger.py` (routing ledger: class, profile, attempts, success, tokens, pool cost); **new** `tools/wd_task_router.py` (classes incl. `planning_synthesis`; expected-cost routing; plan-then-execute return at a safe boundary); **mod** `CLAUDE.md` Rule 8 (amendment; (a)-class). |
| F20 | T | **mod** `waggledance/core/bridge_workflow.py:13-39` (new `grok_consult` role replaces the refusal); **mod** `bridge_capacity_advisor.py:344-345`; **new** `tools/wd_grok_broker.py` (serialized; admission table; composer share; scoped single-use operator exemption record; reply bound to request id and prompt digest; wraps `wd_grok_helper.consult`). |
| F24 | F | **new** `tools/wd_composer_select.py` (pure: eligibility then ranking on a frozen registry snapshot digest; ties, epsilon, plausibility bound; `composer_unknown`/`composer_fallback`). |
| F26 | F writes, T runs | **new** `tools/wd_routing_weights.py` (outcome records → weights with decay; stop-signal quarantine; independent-evaluator quorum; forecast-error recalibration; hard bounds from F0); shadow weights first. |
| F27 | L (with F16/F17) | executor stand-in path (supervisor detection → fence → CAS → reconcile → resume); **new** step journal, WIP checkpoint and operation journal writers `tools/wd_task_journal.py`; hand-back. |

### Stage 6: release tooling

- **F28 Release worktree helper + bundle rollback** (new; owner L;
  `ops/**`, so (a)-class; part of the reviewed v2 package only).
  - **New** `ops/windows/reboot/New-WdReleaseWorktree.ps1`: creates the
    persistent release worktree on a named, pushed branch with its upstream
    set, as the installer requires (`Deploy-WdRebootBundle.ps1:597-617`).
  - **New** `ops/windows/reboot/Restore-WdRebootBundle.ps1`:
    - It fences the affected writers first. It then validates the backward
      compatibility of the persisted state, the outstanding intents and the
      manifest provenance before it reinstalls a previous bundle from
      `C:\Python\wd-reboot-bundles\<sha>`, cold-switch style.
    - It never restores old runtime data and never overwrites new WIP.
      Mixed old and new lanes keep matching pins. Both RCOs stay available.
      The old bundle and the explicit merge-driver HOLD are preserved.
    - A failed rollback is a HOLD, never a loop.
  - **#1751 does not depend on F28.** Its installer stop was a caller setup
    error; a named branch with an upstream at the same commit is enough once
    the operator continues. No unsigned v2 tooling is slipped into the
    current deployment.

**Packaging:** every new Python tool that lanes run at runtime is added to
`ops/windows/reboot/bridge-code-files.json` (entrypoints plus hashes); new
PowerShell files go under `.agent-bridge/bin/`, which the installer already
copies whole.

**Size:** 31 slices (F0-F30 minus F14, plus the contract doc). Most are
small to medium. The large ones are F7, F8, F16, F17 and F27.

## 5. Build order

1. #1751 rollout finished, on its own signed path (a named branch with an
   upstream at c5f7c933 after the operator continues; no v2 tooling).
2. F0 + the interface contract, reviewed.
3. File-disjoint slices, in parallel within a stage, in stage order 1 → 5,
   then F28. Each slice goes on its own branch, gets an isolated test run
   (scrubbed identity, temp runtime root, isolated kernel names), is
   reviewed as it lands, and is composed into one integration branch
   `codex-lead-1/bridge-v2-integration`.
4. Freeze the integration head. Then the **complete exact-head gate set**,
   with nothing reduced:
   - CI 6/6 on Linux at the exact head;
   - the fable-5 Windows matrix in PowerShell 5.1 and 7 (every consumer
     suite found by `git grep`, not from memory);
   - Grok as an advisory, pre-RCO review (never a gate);
   - `build_consensus_pass` from **both Lead and Tools**;
   - `RCO_PASS` from **both** independent RCOs at the exact head;
   - the veto check (`tools/check_bridge_changes_requested.py`) and the
     RCO pass check (`tools/check_rco_pass_present.py`);
   - the canonical consensus verifier (`verify_bridge_consensus` in
     `tools/idle_consensus_auto_merge.py:1285`);
   - the retained unresolved-event checks (accepted-queue preflight,
     `tools/bridge_accepted_queue_preflight.py:141`).

## 6. One signature: what it binds

One operator instruction, given directly in Lead's session and recorded as
the `wd.operator-signed-merge.v1` audit (verbatim text, UTF-8 hash, channel
and time), binds:

1. **Code:**
   - the integration PR number, full head SHA, tree SHA and base (`main`)
     SHA;
   - the tag name `bridge-2026.MM.DD-bridge-v2`;
   - the SHA-256 of the release notes
     (`docs/operations/BRIDGE_V2_RELEASE_NOTES.md`).
2. **Policy artifacts, each by SHA-256:**
   - the `CLAUDE.md` Rule 8 amendment;
   - the signed catalog;
   - `configs/bridge_v2_activation.json` (flags, predicates, parameters,
     absolute expiry, policy bits);
   - the Grok routing and admission table;
   - the list of proposed new authorities (plan §4), named one by one.
3. **Delegation to `codex-lead-1`** for exactly these steps:
   - the squash merge with `--match-head-commit`;
   - tagging;
   - building and staging the bundle;
   - the cold-switch install;
   - lane rollover;
   - staged activation against the predicates;
   - the automatic flag-off and bundle rollback on the stop conditions.

   Today no rule governs deployment, so the signature must name it
   explicitly (`Deploy-WdRebootBundle.ps1:1103-1105` states that reboot
   state grants no deploy permission).
4. **Excluded:**
   - the Stage-2 cutover (Rule 10);
   - Rule 9b activation;
   - approval carry-forward;
   - any budget bypass;
   - fast or credit tiers;
   - new providers or data egress.

## 7. From merge to production (the runbook the signature delegates)

| Step | Command or check | Stop condition |
|---|---|---|
| 1. Pre-merge recheck | Head, tree and base equal the signed values; the complete §5 gate set passes at that head | any mismatch or failure |
| 2. Merge | `gh pr merge <N> --squash --match-head-commit <signed head>` | the command refuses |
| 3. Verify merge | The merge commit's tree equals the signed tree, and its parent equals the signed base | a mismatch means HOLD, and no tag |
| 4. Main CI | Both workflows succeed on the merge commit | any failure means HOLD |
| 5. Release worktree | `New-WdReleaseWorktree.ps1` creates the named branch, pushed, with its upstream | the helper refuses |
| 6. Stage | Under Windows PowerShell 5.1: `Deploy-WdRebootBundle.ps1 -StageOnly -DryRun`, then `-StageOnly`; full recursive verification (:900-965); compare the staged file set and hashes with the reviewed candidate | any refusal or difference |
| 7. Tag + GitHub release | Only after step 6: the tag at the merge commit, a GitHub release targeting that commit, notes whose SHA-256 equals the signed hash; no Docker or stable-image workflow dispatch | mismatch |
| 8. Cold switch | Disable `WD-Supervisor`, wait for its invocation to exit, install without `-StageOnly`, verify `WD_REBOOT_INTEGRITY_CURRENT`, re-enable | a failure inside the installer rolls back the machine transaction files (wrappers, data files and the `WD_REBOOT_*_CURRENT` pointers, `Deploy-WdRebootBundle.ps1:1019-1030`, :1150-1180); the commit-addressed store directory stays and is inert. A failure of the post-install verification has **no** automatic restore until F28's `Restore-WdRebootBundle.ps1`; until then, reinstall the previous signed bundle explicitly, then HOLD |
| 9. Lane rollover | RCO1, RCO2, Tools, Lead, Fable last; never both RCOs together. Readiness per lane is more than the launch probe: token, logon, integrity and ACL gates; the `WD-AgentValue-Weekly` repin; the scheduled supervisor's result; transcript growth; and a bound bridge round trip | a lane fails readiness: stop the rollover; that lane keeps matching old pins. Until the F13 fix lands, every direct `start-wd-agent.ps1` launch gets a child-only standard Windows PowerShell `PSModulePath` |
| 10. Staged activation | Flip F0 flags stage by stage (1 → 5), each with its canary order and coverage predicate, within 14 days of the absolute timestamp | a stop condition means that stage's flags go off automatically |
| 11. Steady state | A stage that met its predicate stays on, under freeze, the kill switch and stop conditions | a regression means flag-off; a bundle fault means `Restore-WdRebootBundle.ps1` |

**Rollback layers, fastest first:**
1. The durable revocation and freeze state (F0), checked before every side
   effect; the environment variable is only an extra local deny.
2. Per-feature F0 flags off.
3. Reinstall the previous bundle with `Restore-WdRebootBundle.ps1`.
4. A source revert, as a new reviewed PR.

## 8. What stays manual or outside

- The operator's one signature.
- Unfreezing after an operator freeze.
- Safety or authority HOLDs whose condition does not clear.
- The Stage-2 cutover.
- Branch-protection settings on `main`; there are none today.

## 9. Open facts (UNVERIFIED unless stated)

- Which checks branch protection requires, if any.
- Where earlier bridge release notes live; no tooling creates the `bridge-*`
  tags.
- The active bundle, per Lead: the known deployment is still d26357e1.
  #1751 (main c5f7c933) has both main CI runs green, but the installer
  DryRun refused the detached HEAD, and no tag, release or activation has
  happened. The #1751 release notes are identified in its signed packet.
- Whether the cause-B latch fix for Rule 9b has landed; it is not needed
  here.
- Whether the Claude capacity hooks are applied in every lane worktree.
- The exact out-of-repo role prompt contents that F2 moves into the repo.

## 10. Review record

- **Lead, 05:32:49Z, on head 0be9caef: `modified`, design review only.**
  - All objections and proposals are adopted above:
    - the complete exact-head gate set, including Tools'
      `build_consensus_pass`;
    - #1751 independent of F28;
    - C1 corrected, with exact references and reproduction first;
    - a durable revocation state rather than an environment kill switch;
    - a runtime-root-scoped mutex, and crash cut points for the outbox;
    - no self-granted flags;
    - a strengthened staging, release, readiness and rollback runbook;
    - F5 participants kept apart from proven handle holders;
    - the current deployment facts.
  - It is not source approval, new authority, or verification of every
    citation.
- **Grok read-only repository review (advisory), 2026-09-28, on head
  249e84c4.**
  - grok-4.7 at effort high, 40 turns, tools `read_file`, `grep` and
    `list_dir` only, on a `git archive` snapshot. It took 528 s, with a peak
    context of 154k tokens. The session log shows 99+ tool calls, all
    allowlisted and none denied.
  - It checked 74 citations and found 72 correct. It reported 9 items;
    fable-5 verified each against the code. 7 were real as stated. 2 were
    partly right: the stale sweep was already named in F23, and the
    installer does roll back the integrity pointers.
  - Adopted: the C6 launch line; three installer places for F2; the
    existing per-claim lock in F8; the owner-check mismatch in F10; the
    stop-port adapter in F16; the full phase and reason map, including
    `resume_pending`, `cancelled_before_apply`, and a rollback landing in
    `verified`, which fable-5 found while verifying; the reserved-label
    callers in F23; and the restore wording in step 8.
  - A first attempt at 8ea1e5c8 produced nothing: `--deny WebSearch` in Grok
    0.2.14 also blocks `read_file` and `list_dir` (bisected by probe), and
    20 turns ran out.
- **#1751 rollout observations folded in (fable-5, read-only):**
  - the missing `PSModulePath` normalization in `start-wd-agent.ps1` (F13,
    step 9);
  - the same-source PID and start-time comparison (F16).
