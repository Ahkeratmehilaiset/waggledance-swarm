# v3.13.0 Schema Bundle

This bundle is Sprint 1 Band A. It adds machine-checkable contracts for the
v3.13.0 foundation layer without enabling runtime writeback.

Included schemas:

- `tool_descriptor.schema.json` for runnable tool manifests.
- `state_handle.schema.json` for current state and projection descriptors.
- `authenticated_connector.schema.json` for secret-free connector capability.
- `mfa_policy.schema.json` for MFA checkpoint declarations.
- `recovery_capsule.schema.json` for rollback and rebuild contracts.
- `provider_registry.schema.json` for seed/provider contribution records.
- `profile_config.schema.json` for deployment profile context and overrides.
- `solver_candidate_manifest.schema.json` for SCH-005 shadow/hybrid
  candidate manifests, provenance signatures, and activation state.
- `domain_catalog.schema.json` for generated domain projections.

The `AuthenticatedConnector` schema deliberately stores `credential_ref` only.
Credential material stays behind `CredentialVault` and is not represented in
this bundle.

`tools/build_v3_13_domain_catalog.py` builds `DomainCatalog` rows from
`ToolDescriptor` and `StateHandle` inventories. The output is derived state;
descriptor and state inventories remain the source records.

`tools/build_v3_13_inventories.py` creates seed `ToolDescriptor` and
`StateHandle` inventories from tracked path names. It does not read file
contents. This keeps the first inventory pass useful for catalog projection
without ingesting credentials, browser profiles, personal documents, or runtime
data.

Boundary decisions from the Sprint 1 RCO:

- `external_readonly` state handles cannot declare writers or write modes.
- External-effect write classification is driven by connector risk or
  `external_system`, not by a contradictory read-only state plane.
- `informational_artifact` state handles are writable advisory outputs that
  classify as informational in `WriteRCOGate`; they still require state
  resolution, credential scanning, and `write_modes_allowed` checks.
- Runtime audit event names such as `write.intent_approved` are MAGMA/domain
  events. Bridge messages remain coordination events such as
  `handoff/rco_requested`.
- Credential scanning belongs both at write-gate time and in a tracked guard
  tool; local Git hooks are optional wrappers, not source of truth.
