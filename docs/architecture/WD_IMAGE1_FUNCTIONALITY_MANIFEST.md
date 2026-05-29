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
  allowlist, queue, scheduler, operator metrics, a read-only dashboard ops
  overlay with local alert state, operator alert thresholds, and proof
  fixtures; do not imply unrestricted runtime authority.

## Capability Matrix

| Capability | Safe status | Repo evidence | Smallest next work |
| --- | --- | --- | --- |
| Hex-mesh routing | Partial, with route-order proof, HTTP/WS trace contract, dashboard route-stage label smoke, operator route-stage count metrics, runtime rate/latency counters, p95/p99 PromQL panel templates from sanitized histograms, and an optional sanitized read-only Prometheus/Alertmanager latency feed provider with timeout, credential, and private-host guardrails | `waggledance/core/hex_cell_topology.py`, `configs/hex_cells.yaml`, `docs/architecture/HEX_TOPOLOGIES.md`, `waggledance/adapters/http/routes/chat.py`, `waggledance/adapters/http/routes/metrics.py`, `waggledance/adapters/http/routes/compat_dashboard.py`, `waggledance/adapters/http/route_stage_latency_feed.py`, `web/hologram-brain-v6.html` | Add provider health/cache metrics and bounded backoff without adding route controls. |
| Deterministic solver-first routing | Partial, with opt-in receipt binding proof | `waggledance/core/reasoning/solver_router.py`, `docs/architecture/HONEYCOMB_SOLVER_SCALING.md` | Promote solver trace receipt coverage from opt-in proof to configured runtime coverage and exported metrics. |
| MAGMA audit log | Partial, with opt-in solver-trace receipt proof, a contract-first no-payload cross-instance share manifest, an explicit operator-gated local share exporter, a no-authority replay importer, an operator-owned peer-review handoff artifact for import decisions, and a read-only bounded `/api/ops` / hologram status history plus provider health, freshness/retention thresholds, an operator-owned feed freshness source, privacy-safe `/metrics` gauges for that handoff feed, read-only provider metrics alert thresholds, an optional configured MAGMA handoff metrics Alertmanager feed with timeout, credential, private-host, cache-health, bounded-backoff, SLO-panel, drill-evidence, and manual release-gate guardrails, plus explicit local package, validator, reviewer-handoff summary, bridge-event template, local reviewer handoff bundle index, local reviewer handoff bundle verifier, local reviewer handoff bundle verification summary, local operator decision-reference validator, local operator decision-reference review summary, local operator decision-reference review bundle index, local operator decision-reference review bundle verifier, local operator decision-reference review bundle verification summary, local operator decision-reference review bundle verification bridge-event template, local operator decision-reference review bundle verification bridge-event template index-entry, verifier, verification summary, and verification-summary bridge-event template tools for operator-owned release review | `waggledance/core/magma/event_log_adapter.py`, `waggledance/core/magma/receipt_bundle.py`, `waggledance/core/magma/runtime_summary_receipt.py`, `waggledance/core/magma/share_manifest.py`, `tools/export_magma_share_manifest.py`, `tools/import_magma_share_manifest.py`, `tools/package_magma_alert_feed_release_evidence.py`, `tools/validate_magma_alert_feed_release_evidence.py`, `tools/build_magma_alert_feed_reviewer_handoff_summary.py`, `tools/build_magma_alert_feed_reviewer_bridge_event_template.py`, `tools/build_magma_alert_feed_reviewer_handoff_bundle_index.py`, `tools/verify_magma_alert_feed_reviewer_handoff_bundle_index.py`, `tools/build_magma_alert_feed_reviewer_handoff_bundle_verification_summary.py`, `tools/validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference.py`, `tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary.py`, `tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index.py`, `tools/verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index.py`, `tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary.py`, `tools/build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template.py`, `tools/build_magma_decision_review_verification_template_index_entry.py`, `tools/verify_magma_decision_review_verification_template_index_entry.py`, `tools/build_magma_decision_review_verification_template_index_entry_summary.py`, `tools/build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template.py`, `waggledance/adapters/http/routes/compat_dashboard.py`, `waggledance/adapters/http/routes/metrics.py`, `waggledance/adapters/http/magma_handoff_metrics_alert_feed.py`, `web/hologram-brain-v6.html`, `schemas/v3_13_0/magma_share_manifest.v0.json`, `docs/operations/MAGMA_HANDOFF_PROVIDER_METRICS_RUNBOOK.md`, `docs/architecture/MAGMA_SHARE_MANIFEST_CONTRACT.md`, `docs/architecture/CONTROL_PLANE_AND_DATA_PLANE.md` | Add a local index entry for the operator decision-reference review bundle verification bridge-event template index-entry verification summary bridge-event template without appending it. |
| Low-risk autonomy loop | Partial, with temp-DB proof, runtime-boundary smoke, operator metrics, a read-only dashboard ops overlay with local alert state, and operator alert thresholds | `waggledance/core/autonomy_growth/low_risk_policy.py`, `runtime_query_router.py`, `autogrowth_scheduler.py`, `waggledance/bootstrap/container.py`, `waggledance/adapters/http/api.py`, `waggledance/adapters/http/routes/metrics.py`, `waggledance/adapters/http/routes/compat_dashboard.py`, `web/hologram-brain-v6.html`, `docs/operations/LOW_RISK_AUTOGROWTH_RUNBOOK.md` | Wire a real Prometheus/Alertmanager feed into the read-only Ops alert state without adding controls. |
| Hexagonal upgrades | Partial, with in-memory proof and runtime-boundary smoke | `waggledance/core/hex_topology/subdivision_operator.py`, `ring_messaging.py`, `parent_child_relations.py`, `waggledance/bootstrap/container.py`, `hex_topology_registry.py`, `hex_neighbor_assist.py` | Promote hexagonal topology boundary reporting into operator-visible metrics without enabling runtime mutation. |
| Future swarm scalability | Partial, with scale-axis scorecard proof | `docs/architecture/explosive_intelligence_growth_2.md`, `docs/architecture/HONEYCOMB_SOLVER_SCALING.md`, `tools/wd_image1_capability_manifest.py` | Populate the scale-axis scorecard from runtime metrics and benchmark artifacts. |

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
  `deterministic_solver_first`, `magma_audit_log`, `hexagonal_upgrades`,
  `low_risk_autonomy_loop`, and `future_waggledance_swarm`

The `hex_mesh_entry` proof is a route-boundary proof, not a literal claim
approval. It reads `configs/settings.yaml`, loads both hex topologies, verifies
representative 8-cell solver-retrieval and 7-cell agent-routing assignments,
and reports the current chat route order. The `/api/chat` contract and
dashboard `/ws` `chat_route` event expose the same privacy-safe
`route_stage_trace` emitted by `ChatResult`; the WS event also adds
`route_stage_labels` and `disabled_route_stages` so clients can inspect route
stages without receiving raw query, language hint, or profile values. The
dashboard chat panel renders those labels through a local stage-name allowlist
and escapes stage/status text; it does not render backend-supplied free-form
labels or raw route trace payloads. `/metrics` also exposes
`waggledance_route_stage_count{group=...}` gauges derived from the static
route-stage allowlist and optional component flags, plus
`waggledance_route_stage_observations_total{stage=...}` and
`waggledance_route_stage_request_latency_ms_total{stage=...}` counters recorded
only after the HTTP chat response has been sanitized. The route-stage latency
histogram `waggledance_route_stage_request_latency_histogram_ms_bucket{stage=...,le=...}`
uses the same whitelisted stage labels and supports p95/p99
`histogram_quantile(...)` panels. The observation counter supports Prometheus
`rate(...)` by stage. The latency metrics are stage-correlated request-latency
measurements, not internal span timers; divide the sum by observations for an
average per observed stage. `/api/ops` and the hologram Ops panel expose the
p95/p99 panel and alert PromQL as read-only templates, while the operator
runbook defines conservative p95/p99 thresholds. `/api/ops` also accepts an
optional configured `route_stage_latency_feed` provider and exposes only
sanitized feed state: known panel IDs, known alert IDs, fixed route-stage
labels, numeric values, timestamps, and WD-generated summaries. The provider is
disabled by default, refuses credential-bearing URL/header shapes, and requires
explicit allowlisting for private or localhost hosts. It drops raw Alertmanager
annotations, unknown labels, raw URL details, raw queries, profile/language
data, hostnames, paths, and exception details. These metrics, templates, and
feed state do not enable disabled hex paths or add mutating controls.
Current settings have `hybrid_retrieval.enabled=true` in `candidate` mode and
`hex_mesh.enabled=false`, so the literal "every query first enters the mesh"
wording remains unsafe.

The `hexagonal_upgrades` proof is intentionally pure. It builds a temporary
topology in memory, applies a shadow subdivision plan, verifies parent/child
relations, and delivers ring / parent-to-child / child-to-parent messages
without changing runtime topology or files. It also runs a runtime-boundary
smoke through `Container.hex_topology_registry`, reports the current
`hex_mesh.enabled` dispatch gate and 7-cell runtime config topology, and
verifies that the shadow child cells from the pure proof are not inserted into
the runtime config. This does not enable runtime subdivision authority.

The `low_risk_autonomy_loop` proof is intentionally local. It creates an
ephemeral control-plane database, records one low-risk runtime miss, digests it
into an allowlisted growth intent, runs one bounded scheduler tick, verifies the
promoted solver serves the next matching query, then deletes the temp database.
It now also runs a runtime-boundary smoke that constructs the configured
`AutogrowthBackgroundTicker` through `Container`, reports the default 30 second
cadence and 20 ticks per wake limit, and verifies that FastAPI lifespan contains
start/stop hooks for that ticker. It does not change production runtime
authority or write tracked files. The proof also checks that `/metrics` exposes
the same boundary under the `waggledance_autogrowth_*` Prometheus namespace:
source health, ticker configured/running state, cadence, max ticks per wake,
wakeups, non-idle ticks, and errors. Those metrics are observability only; they
do not enable additional runtime mutation or solver growth authority. The
hologram Ops panel also renders the same ticker boundary as read-only status
cards from `/api/ops`; it exposes enabled/running state, cadence, max ticks per
wake, wakeups, non-idle ticks, and errors without start/stop or configuration
controls. `/api/ops` also exposes `autogrowth.alert_state`, a read-only local
snapshot for the dashboard. It can flag source-down and observed-error states
from the current Ops counters, while time-window rules remain deferred to a
Prometheus/Alertmanager feed. The operator runbook defines conservative
Prometheus thresholds for source health, error increases, wakeup stalls, wakeup
bursts, and non-idle burst rates; those alerts are evidence-collection triggers
and do not call mutating endpoints or grant runtime authority.

The solver/MAGMA receipt proof is also local and opt-in. It runs
`AutonomyRuntime.handle_query` with a runtime receipt sink, writes a temporary
MAGMA receipt bundle, verifies it offline, checks that the sanitized
`solver_call_trace` is digest-bound by the receipt payload, and then deletes
the temp artifacts. It does not prove default receipt emission for every
solver path.

`magma.share_manifest.v0` is a contract-first cross-instance sharing boundary,
not default runtime export. It requires `runtime_export_enabled=false`,
`payload_visibility=no_payload`, zero payload files, no payload digests, and an
explicit absence inventory for raw payloads, replacement maps, raw context, raw
solver output, and raw-query digests. The explicit
`tools/export_magma_share_manifest.py` path is operator-gated and validates the
schema, date-time formats, and artifact-count consistency before writing a
local `share_manifest.json`; it does not copy payload files or enable default
runtime receipt emission. `tools/import_magma_share_manifest.py` consumes that
manifest only as no-authority replay metadata, rejects stale manifests, and
rejects context-drifted receipt/EvaluationResult references against a local
receipt-bundle manifest. When explicitly requested, it can also write a local
`share_import_peer_review_handoff.json` artifact that records an
operator-owned import decision, digest bindings, and replay metadata refs. That
handoff keeps runtime export disabled, grants no runtime authority, records no
local paths, and does not copy payload files. `/api/ops` and the hologram Ops
panel can now render a read-only `magma_share_import_handoff` summary from an
explicitly configured snapshot or bounded snapshot history. The summary is
disabled by default, validates every history entry before retention truncation,
performs no disk scanning, exposes only digest/categorical refs plus
no-authority flags, and adds no runtime controls. `/api/ops` also includes
`provider_health` for the explicit peer-review handoff feed: configured,
available, valid, snapshot-kind, count, retained/dropped, and sanitized warning
IDs only. It now also exposes read-only freshness/retention alert thresholds
and deterministic retention-window warnings when the bounded history drops
entries. Provider health can also consume an explicitly configured
operator-owned freshness source for the peer-review handoff feed. That source
is sanitized down to fixed timestamp/count/window/state fields plus the fixed
`operator_peer_review_handoff_feed` label, can raise stale/invalid/unavailable
warning IDs, and does not expose raw paths, URLs, arbitrary source labels,
provider exception details, local paths, payloads, or controls. The provider
health and freshness source state are also promoted into privacy-safe
`waggledance_magma_handoff_*` operator metrics. Those gauges use fixed
status/freshness/alert label sets and do not publish timestamps, share IDs,
operator decision IDs, paths, URLs, arbitrary source labels, raw provider
summaries, exception details, payloads, or controls. The MAGMA handoff provider
metrics runbook defines read-only Prometheus checks for source health, snapshot
validity,
freshness staleness, retention drops, private-material flags, runtime-authority
flags, and payload-import flags. Those alerts do not call mutating endpoints,
import payloads, add import controls, or grant runtime authority. `/api/ops`
can now expose `provider_health.metrics_alert_state` from an explicit
MAGMA handoff metrics Alertmanager feed. It accepts only fixed runbook alert
IDs, warning/critical severities, finite numeric samples, and sanitized ISO
timestamps; it drops raw Alertmanager labels, annotations, URLs, paths,
hostnames, unknown alert IDs, resolved alerts, and provider exception details.
The configured adapter is disabled by default, reads only Alertmanager
`/api/v2/alerts`, refuses credential-bearing URL/header shapes, requires
explicit allowlisting for private or localhost hosts, and reports bounded
cache/backoff provider health through `/api/ops` and fixed-label `/metrics`
gauges/counters. The same Ops surface now includes fixed PromQL SLO panel
templates, a drill-evidence checklist for collecting safe artifacts during
operator review, and manual release-gate examples that consume that evidence
without adding merge, promotion, configuration, importer/exporter, or runtime
controls. Operators can now package an explicit local `/api/ops` JSON snapshot
and explicit local `/metrics` scrape into sanitized JSON/Markdown evidence
artifacts with `tools/package_magma_alert_feed_release_evidence.py`; the
package records only digests, allowlisted SLO metadata, current metric samples,
active alert IDs, and manual hold reasons, with no endpoint fetches, raw
payloads, raw scrapes, raw Alertmanager labels, local paths, automatic release
decision, or runtime controls. Reviewers can validate the package and optional
local artifact digests with
`tools/validate_magma_alert_feed_release_evidence.py`; it writes nothing,
fetches no endpoints, transports no artifacts, and also makes no automatic
release decision. Operators can render a sanitized reviewer handoff summary
from the validated local package and local validation report with
`tools/build_magma_alert_feed_reviewer_handoff_summary.py`; it prints context
only, carries validation status and manual-gate hold reasons, and keeps
`approval_granted=false`, `release_decision_made=false`, and
`automatic_release_decision=false`. Operators can also render an optional
bridge-event template from that sanitized summary with
`tools/build_magma_alert_feed_reviewer_bridge_event_template.py`; the template
validates as bridge `handoff` JSON but is `template_only=true`, performs no
direct bridge write, and keeps `approval_granted=false`,
`release_decision_made=false`, and `automatic_release_decision=false`. The
template can carry an optional operator decision-reference slot as context
only, with `decision_reference_is_approval=false` and
`decision_reference_is_release_decision=false`. A local reviewer handoff bundle
index can tie the package, validation report, reviewer summary, and
bridge-event template digests while keeping `artifact_payloads_included=false`,
`local_paths_recorded=false`, `transport_added=false`,
`approval_granted=false`, and `release_decision_made=false`. A local reviewer
handoff bundle verifier can recompute `digest_checks`,
`schema_version_checks`, and size checks from explicit local artifacts while
keeping those same no-payload/no-path/no-transport/no-approval boundaries. The
local reviewer handoff bundle verification summary renderer can read that
verifier JSON and render `verification_ok`, digest/size/schema status, and
sanitized blocker tokens for reviewer handoff while keeping
`direct_bridge_write_performed=false`, `artifact_payloads_included=false`,
`local_paths_recorded=false`, `transport_added=false`,
`approval_granted=false`, and `release_decision_made=false`. The local
operator decision-reference validator can then compare the bundle
bridge-event template reference with the expected sanitized operator-owned
reference and the verified bundle summary while keeping
`decision_reference_is_approval=false`,
`decision_reference_is_release_decision=false`, `approval_granted=false`, and
`release_decision_made=false`. A local operator decision-reference review
summary can then render that validator result into path-free reviewer context,
carry `decision_reference_validated`, and keep the operator decision separate
while preserving the same no-approval/no-release-decision boundary. A local
operator decision-reference review bundle index can then tie the validation
report and review summary digests while keeping `artifact_payloads_included=false`,
`local_paths_recorded=false`, `transport_added=false`,
`approval_granted=false`, and `release_decision_made=false`. A local operator
decision-reference review bundle verifier can then recompute `digest_checks`,
size checks, and `schema_version_checks` from explicit local artifacts while
preserving the same no-payload/no-path/no-transport/no-approval boundary. A
local operator decision-reference review bundle verification summary can then
render `source_contract_check`, `rebuilt_index_check`, and
`decision_reference_verified` as path-free reviewer context while preserving
the same no-payload/no-path/no-transport/no-approval boundary. A local
operator decision-reference review bundle verification bridge-event template
can then render that verified summary as schema-valid handoff JSON while
preserving the same no-payload/no-path/no-transport/no-approval boundary and
without appending it. The next small step is adding a local operator
decision-reference review bundle verification bridge-event template index
entry with
`tools/build_magma_decision_review_verification_template_index_entry.py`.
The index entry records `template_index_entry`, `bridge_event_schema_validated`,
`source_contract_check`, and `rebuilt_template_check` while preserving the same
no-payload/no-path/no-transport/no-approval boundary and without appending it.
This local operator decision-reference review bundle verification bridge-event template index entry remains context-only.
The local index-entry verifier can then recompute `digest_checks`, size checks,
`schema_version_checks`, `rebuilt_index_entry_check`, and
`bridge_event_schema_check` while preserving the same
no-payload/no-path/no-transport/no-approval boundary and without appending it.
This local operator decision-reference review bundle verification bridge-event template index-entry verifier remains context-only.
The local operator decision-reference review bundle verification bridge-event template index-entry verification summary can then turn that verifier result into path-free reviewer context with `source_contract_check`, `rebuilt_index_entry_check`, `bridge_event_schema_check`, `decision_reference_verified`, and `template_only` while preserving the same no-payload/no-path/no-transport/no-approval boundary and without appending it.
A local bridge-event template for that summary can then render schema-valid
handoff JSON with the same verified checks while preserving the same
no-payload/no-path/no-transport/no-approval/no-release-decision boundary and
without appending it.
This local operator decision-reference review bundle verification bridge-event template index-entry verification summary bridge-event template remains context-only.
The next small step is adding a local index entry for that summary
bridge-event template without appending it.

### Future Scale-Axis Scorecard

The `future_waggledance_swarm` proof is a scale-axis scorecard, not a
scalability claim. It decomposes the image phrases "emergent intelligence",
"infinite scalability", and "industrial-grade efficiency" into measurable
axes from `HONEYCOMB_SOLVER_SCALING.md`: coverage, LLM fallback rate, route
depth, useful composite paths, contradiction rate, insight score, latency, and
audit completeness. It also checks that EIG2 remains disabled by default and
that scale simulation is benchmark-only. The literal future phrases remain
unsafe until those axes are populated by versioned runtime metrics and
benchmark artifacts.

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
