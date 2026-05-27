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
- Do not claim hard MAGMA append-only enforcement. Current architecture docs
  state that append-only is still convention for some storage paths.
- Do not claim unlimited scalability. The repo supports planned and measured
  scalability work, not an unbounded guarantee.
- Low-risk autogrowth may be described as a bounded substrate with an
  allowlist, queue, scheduler, and proof fixtures; do not imply unrestricted
  runtime authority.

## Capability Matrix

| Capability | Safe status | Repo evidence | Smallest next work |
| --- | --- | --- | --- |
| Hex-mesh routing | Partial | `waggledance/core/hex_cell_topology.py`, `configs/hex_cells.yaml`, `docs/architecture/HEX_TOPOLOGIES.md` | Add an end-to-end proof that shows the exact query entry order for the active runtime flags. |
| Deterministic solver-first routing | Partial | `waggledance/core/reasoning/solver_router.py`, `docs/architecture/HONEYCOMB_SOLVER_SCALING.md` | Add per-solver-call trace coverage so "full MAGMA provenance" becomes measurable. |
| MAGMA audit log | Partial | `waggledance/core/magma/event_log_adapter.py`, `waggledance/core/magma/receipt_bundle.py`, `docs/architecture/CONTROL_PLANE_AND_DATA_PLANE.md` | Harden append-only enforcement or keep user-facing wording at "audit/provenance wrappers". |
| Low-risk autonomy loop | Partial | `waggledance/core/autonomy_growth/low_risk_policy.py`, `runtime_query_router.py`, `autogrowth_scheduler.py` | Add a read-only runtime proof that a gap signal can flow to an allowlisted growth intent without external authority. |
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

The command is read-only. It emits a JSON matrix with:

- `status`: `implemented`, `partial`, `planned`, or `blocked`
- `claim_safe`: whether the image's literal claim is safe to repeat
- `evidence`: repo paths used for the status
- `gaps`: why stronger wording is not yet supported
- `next_smallest_pr`: the next scoped implementation step
- `proof`: optional non-mutating proof payload for capabilities that have an
  executable local proof, currently `hexagonal_upgrades`

The `hexagonal_upgrades` proof is intentionally pure. It builds a temporary
topology in memory, applies a shadow subdivision plan, verifies parent/child
relations, and delivers ring / parent-to-child / child-to-parent messages
without changing runtime topology or files.

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
