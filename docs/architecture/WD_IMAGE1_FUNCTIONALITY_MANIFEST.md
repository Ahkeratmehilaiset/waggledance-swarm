# WD Image 1 Functionality Manifest

**Status:** implementation manifest and audit guard for Image #1 claims.
**Companion tool:** `tools/wd_image1_capability_manifest.py`

This document turns the Image #1 storyboard into repo-grounded work. It is
not marketing copy. Each image claim is treated as a capability target, then
mapped to code and docs that either prove it, partially support it, or show
that the claim is still a future target.

## Claim Policy

Use the safest true wording until the proof tool reports otherwise:

- Do not claim that every query first enters an 8-cell mesh. The repo has two
  independent hex topologies: a 7-cell agent-routing topology and an 8-cell
  solver-retrieval topology.
- Do not claim hard MAGMA append-only/default enforcement. The repo now has an
  opt-in runtime summary receipt proof that binds sanitized solver traces, but
  some MAGMA storage paths still rely on append-only convention.
- Do not claim unlimited scalability. The repo supports planned and measured
  scalability work, not an unbounded guarantee.
- Low-risk autogrowth may be described as a bounded substrate with an
  allowlist, queue, scheduler, and proof fixtures; do not imply unrestricted
  runtime authority.

## Capability Matrix

| Capability | Safe status | Repo evidence | Smallest next work |
| --- | --- | --- | --- |
| Hex-mesh routing | Partial, with route-order proof and HTTP/WS trace contract | `waggledance/core/hex_cell_topology.py`, `configs/hex_cells.yaml`, `docs/architecture/HEX_TOPOLOGIES.md` | Render the WS route-stage labels in the dashboard UI and add a visual contract smoke. |
| Deterministic solver-first routing | Partial, with opt-in receipt binding proof | `waggledance/core/reasoning/solver_router.py`, `docs/architecture/HONEYCOMB_SOLVER_SCALING.md` | Promote solver trace receipt coverage from opt-in proof to configured runtime coverage and exported metrics. |
| MAGMA audit log | Partial, with opt-in solver-trace receipt proof | `waggledance/core/magma/event_log_adapter.py`, `waggledance/core/magma/receipt_bundle.py`, `waggledance/core/magma/runtime_summary_receipt.py`, `docs/architecture/CONTROL_PLANE_AND_DATA_PLANE.md` | Harden append-only/default enforcement or keep user-facing wording at opt-in audit/provenance wrappers. |
| Low-risk autonomy loop | Partial, with temp-DB proof | `waggledance/core/autonomy_growth/low_risk_policy.py`, `runtime_query_router.py`, `autogrowth_scheduler.py` | Wire the proof into a runtime-facing smoke that reports scheduler cadence and authority boundaries. |
| Hexagonal upgrades | Partial, with in-memory proof | `waggledance/core/hex_topology/subdivision_operator.py`, `ring_messaging.py`, `parent_child_relations.py` | Wire the pure proof into a read-only runtime-facing smoke that reports current config and active topology boundaries. |
| Future swarm scalability | Planned | `docs/architecture/explosive_intelligence_growth_2.md`, `docs/architecture/HONEYCOMB_SOLVER_SCALING.md` | Replace broad future claims with measurable scale axes and gate them with proof artifacts. |

## Agent Work Split

`codex-lead-1` owns this manifest, claim safety, and PR sequencing.

`claude-rco-1` should review the manifest for overclaim risk, especially the
MAGMA append-only statement, low-risk autonomy boundaries, and scalability
wording.

`codex-tools-1` should review the proof tool and propose any missing
capability probes that can be added without touching active PR #706 scope.

## Proof Command

Run:

```powershell
python tools\wd_image1_capability_manifest.py --json
```

The command is read-only with respect to repo files, runtime state, bridge
state, and GitHub state. Executable local proofs may create ephemeral temp
artifacts and delete them before returning. It emits a JSON matrix with:

- `status`: `implemented`, `partial`, `planned`, or `blocked`
- `claim_safe`: whether the image's literal claim is safe to repeat
- `evidence`: repo paths used for the status
- `gaps`: why stronger wording is not yet supported
- `next_smallest_pr`: the next scoped implementation step
- `proof`: optional non-mutating proof payload for capabilities that have an
  executable local proof, currently `hex_mesh_entry`,
  `deterministic_solver_first`, `magma_audit_log`, `hexagonal_upgrades`, and
  `low_risk_autonomy_loop`

The `hex_mesh_entry` proof is a route-boundary proof, not a literal claim
approval. It reads `configs/settings.yaml`, loads both hex topologies, verifies
representative 8-cell solver-retrieval and 7-cell agent-routing assignments,
and reports the current chat route order. The `/api/chat` contract and
dashboard `/ws` `chat_route` event expose the same privacy-safe
`route_stage_trace` emitted by `ChatResult`; the WS event also adds
`route_stage_labels` and `disabled_route_stages` so clients can inspect route
stages without receiving raw query, language hint, or profile values. Current
settings have
`hybrid_retrieval.enabled=true` in `candidate` mode and `hex_mesh.enabled=false`,
so the literal "every query first enters the mesh" wording remains unsafe.

The `hexagonal_upgrades` proof is intentionally pure. It builds a temporary
topology in memory, applies a shadow subdivision plan, verifies parent/child
relations, and delivers ring / parent-to-child / child-to-parent messages
without changing runtime topology or files.

The `low_risk_autonomy_loop` proof is intentionally local. It creates an
ephemeral control-plane database, records one low-risk runtime miss, digests it
into an allowlisted growth intent, runs one bounded scheduler tick, verifies the
promoted solver serves the next matching query, then deletes the temp database.
It does not change production runtime authority or write tracked files.

The solver/MAGMA receipt proof is also local and opt-in. It runs
`AutonomyRuntime.handle_query` with a runtime receipt sink, writes a temporary
MAGMA receipt bundle, verifies it offline, checks that the sanitized
`solver_call_trace` is digest-bound by the receipt payload, and then deletes
the temp artifacts. It does not prove default receipt emission for every
solver path.

## Bridge Handoff Template

After implementation, request RCO and tools review with bridge events under
task id `wd-image1-functionality-manifest-2026-05-27`.

Claude review request:

```powershell
.\.agent-bridge\bin\Write-AgentEvent.ps1 `
  -Agent codex-lead-1 `
  -Type peer_review_request `
  -TaskId wd-image1-functionality-manifest-2026-05-27 `
  -Status rco_requested `
  -Severity medium `
  -To claude-rco-1 `
  -Message "Please review the WD Image #1 manifest and proof tool for overclaim risk. Focus on MAGMA append-only truth, low-risk autonomy authority, two hex-topology wording, and scalability claims." `
  -Role lead-impl
```

Tools review request:

```powershell
.\.agent-bridge\bin\Write-AgentEvent.ps1 `
  -Agent codex-lead-1 `
  -Type message `
  -TaskId wd-image1-capability-proof-tool-2026-05-27 `
  -Status review_requested `
  -Severity medium `
  -To codex-tools-1 `
  -Message "Please review tools/wd_image1_capability_manifest.py and tests for missing read-only probes. Keep clear of active PR #706 scope." `
  -Role lead-impl
```
