# Bridge-only affected-test boundary

The operator authorized a separate Bridge test boundary for Bridge, launcher,
package and message changes. This does **not** waive the product-wide full-suite
CI gate when a change touches WD/product source, shared test configuration, or
an unknown file. The selector is a routing aid; it does not grant merge,
release or deployment authority.

`tools/select_affected_tests.py` contains the reviewed source-to-test map in
`BRIDGE_EXPLICIT_TESTS`. The original eight integration paths were checked
against PR #1755 at exact head `57d4b894591dcf635f1ed984b29b311bc69e0e77`.
The later 14-source/11-test candidate path list was read from `git diff
--name-only c5f7c933 ec76be8490ae8fe01ee27920b5dc98d048470e42` in the
Lead worktree; the additional Fable exact-incoming test was inspected in the
Fable worktree at `b56430b3c272a91ea13423de5f138d76598fe200` (dirty
working file). CI must use its own exact committed head, not these observations.

| Source | Required test families |
| --- | --- |
| `.agent-bridge/bin/BridgeEventClassifier.ps1` | routing, classifier, interim reply, task result, continuity alert, reboot bundle |
| `.agent-bridge/bin/Invoke-StaleClaimSweep.ps1` | stale routing |
| `ops/windows/reboot/Get-WdSwarmParallelStatus.ps1` | continuity status, swarm status, reboot bundle |
| `ops/windows/reboot/Send-WdContinuityAlert.ps1` | continuity alert, native Tools wake |
| `ops/windows/reboot/bridge-code-files.json` | package closure, code context, launch profile, Grok helper, native Tools wake |
| `ops/windows/reboot/start-wd-agent.ps1` | Lead delivery, conversation/startup recovery, native wake, launch and bundle consumers |
| `ops/windows/reboot/start-wd-tools-consumer.ps1` | native Tools wake, Lead reply delivery, inbox/wake, startup recovery, launch and bundle consumers |
| `ops/windows/reboot/Deploy-WdRebootBundle.ps1` | package/code context, final acceptance, dynamic startup, Grok helper, reboot bundle |

The later candidate adds `Get-BridgeRequestInventory.ps1` (inventory test),
`docs/adr/ADR-continuity-recovery-20260929.md` (wake, controls, Lead import,
native Tools tests), and `tools/bridge_continuity_guard.py` (guard and native
Tools tests). The candidate also changes native wake helper/procedure files.
The new `test_wd_tools_exact_incoming_retrieval.py` reads
`WAKE_PROCEDURE_TOOLS.md` directly (Fable test lines 26, 160-174), so it is a
required mapping for that procedure once actually imported. Until that test
exists and is readable in the checked checkout, the boundary returns `full`.

The native wake helper and two procedure Markdown files were separately checked
against commit `e7488916` and map to native-wake and reboot-bundle tests. Known
fleet JSON files have their own existing bundle tests. This boundary document
maps to the selector contract test. Every exact mapped test
must exist and be readable at the checked-out head. A missing test, unknown
source, mixed Bridge/product change, or broad-impact configuration returns
`full_suite=True`; no `.ps1` or directory prefix is blanket-exempted.

The map includes consumers that dynamically load PowerShell functions or import
shared Python test helpers: for example `test_wd_native_tools_wake.py` imports
`test_wd_startup_recovery.py` and `test_wd_bridge_code_context.py`, and
`test_wd_lead_reply_delivery.py` loads functions from both launchers. These
edges were inspected at the exact candidate tree. A whole-repository filename
search may reveal candidates, but it is **not** evidence that all consumers
were found and is not used to accept a Bridge-only change. New dynamic edges
must be reviewed and added to the explicit map before narrowing.

The shared `test_wd_native_wake_prompt.py` exports `relay_bundle_setup` to
`test_wd_lead_reply_delivery.py` (line 12) and
`test_wd_native_tools_wake.py` (line 718). The native-wake test also imports
`notice_registry` back from native Tools, while native Tools imports the
continuity-alert test's `MOCK_WRITER`. The reviewed fixed-point consumer map
expands these test-only and source-selected fixtures; a changed shared test
must not select only itself. `test_bridge_continuity_guard.py` imports the
guard directly (lines 17-18); native Tools stages/loads it (lines 41, 529).
The package manifest names the guard at lines 14 and 40. Unknown test or
source edges remain `full`, never a broad `tests/tools` prefix exemption.

RCO1's exact request `5da5376a-8895-40ea-abd5-eb99f9ebc437` independently
reviewed a 38-file Bridge-only test set at head `57d4b894` and measured 1920
Windows passes. Its classifier reach includes `Watch-Bridge.ps1:114` and
`Monitor-AgentBridge.ps1:130`; the explicit classifier mapping therefore also
requires `test_bridge_session_watcher_probe.py` and
`test_session_liveness_supervisor_report.py`. That measured run does **not**
cover the later prompt/inventory/incoming tests or this revised map.

The three shared test modules `test_wd_reboot_bundle.py`,
`test_wd_startup_recovery.py`, and `test_wd_bridge_code_context.py` provide
PowerShell shell/path/load helpers or bundle fixtures to many Bridge tests.
Actual ec76 imports include native Tools lines 12-14 and 36, Lead reply lines
10-12, and native prompt lines 44, 204-205, 285-286. The selector/router use
their conservative explicit provider closure: the RCO1 38-file Bridge set,
later prompt/inventory/incoming tests, the selector contract, and two further
direct importers inspected at ec76:
`test_bridge_request_preflight.py:9-10` (Bridge request contract) and
`test_wd_task_console_containment_pin.py:9-10` (verified Bridge pin).
Each provider-only regression asserts the 38-set is included; a missing
consumer returns `full`. A new test consumer outside this reviewed set must
be added with evidence or its changed path forces `full`; this is not a
general `tests/tools` allowlist. A source containing the provider name is
only discovery evidence, never a substitute for the explicit map.

`tools/classify_bridge_ci_scope.py` is the read-only, exact-commit CI router.
It requires a clean checkout (including staged/untracked status), matching
loaded classifier/map bytes, an exact NUL-delimited git diff and readable
mapped tests. This document/selector/router still make **no CI workflow
change**; independent review of the complete candidate and fixture closure
is required before wiring.

To inspect a proposed change, run the selector with the exact changed paths:

```text
python tools/select_affected_tests.py --files <repo-relative paths> --json
```

Run the returned test files only if `full_suite` is false, and report the exact
head, paths and command. The caller/CI integration must separately enforce
this decision. This document and selector alone do not change CI behavior.
