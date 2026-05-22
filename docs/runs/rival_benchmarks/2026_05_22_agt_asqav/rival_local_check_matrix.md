# V12 Rival Local Check Matrix

- report_version: `wd.v12.rival_local_check_matrix.v0`
- evidence_manifest_contract_version: `wd.v12.rival_local_evidence_manifest.v1`
- generated_at_utc: `2026-05-22T18:27:04Z`
- pilot_status: `scope_ready_not_consensus_grade`
- consensus_grade: `false`
- rival local checks passed: `1/4`
- blocked rival local checks: `3`

This is not a competitor benchmark. It is a local evidence gate for the
competitor-axis pilot. Rows remain non-consensus-grade until pinned local
evidence manifests prove a rival-side smoke or inspection without a cloud
dependency.

| Rival | Local status | Evidence manifest | Blocker | Required check |
|---|---|---|---|---|
| JamJet | not_configured | `docs/benchmarks/rival_local_checks/jamjet.json` | evidence manifest missing | Install or inspect a pinned OSS package/repo revision and run one policy/audit/replay smoke with no cloud dependency. |
| Asqav | cloud_dependent | `docs/benchmarks/rival_local_checks/asqav.json` | cloud_dependency is not false | Install or inspect a pinned SDK revision and run one local signing/verify or hash-chain smoke; if server dependency is required, mark cloud-dependent. |
| Microsoft AGT | passed | `docs/benchmarks/rival_local_checks/microsoft-agt.json` | - | Install or inspect a pinned repo/package revision and run one policy deny smoke plus one fail-closed/error-path smoke. |
| Preloop | not_configured | `docs/benchmarks/rival_local_checks/preloop.json` | evidence manifest missing | Install or inspect a pinned OSS component/repo revision and run one MCP allow/deny/approval smoke; if hosted service is required, mark cloud-dependent. |

## Required Evidence Manifest Fields

`evidence_manifest_contract_version`, `rival`, `pinned_revision`, `local_artifact_path`, `local_artifact_sha256`, `smoke_command`, `smoke_result`, `cloud_dependency`, `evidence_type`

A row only passes when `cloud_dependency=false`, `smoke_result=passed`,
`evidence_type` is `local_inspection` or `local_smoke`, and all required
fields are present. The `local_artifact_path` must name an existing
file under the evidence directory and `local_artifact_sha256` must
match that file. The artifact itself must be a machine-readable
offline evidence JSON whose rival, pinned revision, evidence type,
pass status, and rival-specific observations match the manifest.

## Required Evidence Artifact Fields

`evidence_artifact_contract_version`, `rival`, `pinned_revision`, `smoke_result`, `offline`, `ok`, `evidence_type`, `observations`

## Required Rival Observations

`JamJet: policy_audit_or_replay_smoke`, `Asqav: local_sign_or_hash_chain_smoke`, `Microsoft AGT: policy_deny_smoke, fail_closed_error_path_smoke`, `Preloop: mcp_allow_deny_approval_smoke`
