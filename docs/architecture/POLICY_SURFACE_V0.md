# Policy Surface v0

Status: candidate contract for v3.13.0 substrate work.

Policy Surface v0 defines a declarative, canonicalizable policy artifact that can be hashed and referenced by MAGMA receipts. It is not a runtime enforcement rewrite. The existing charter-as-code kernel remains the authority path for refusal, WriteRCOGate behavior, risk-class gating, and external-effect operator approval.

## Why This Exists

MAGMA receipt v1 already has `policy_digest` and `charter_digest`. Without a typed policy artifact, those digests can only point at ad hoc source snapshots or prose. Policy Surface v0 gives WD a stable artifact shape for:

- offline verification in Profile S;
- later Rego/Cedar/YAML export without changing the kernel;
- receipt binding through `policy_digest` and `charter_digest`;
- operator-readable policy diffs.

## Authority Boundary

The required `authority` is `declarative_audit_only_v0`, and `authority_mode` is `declarative_mirror`. The schema also requires:

- `kernel_authoritative: true`;
- `no_runtime_enforcement_change: true`;
- `no_auto_execute_grant: true`;
- `external_effect_operator_gate: true`;
- `offline_profile_s_supported: true`.

These constants are deliberate. A policy surface file that claims runtime authority, disables the operator gate, or depends on online services is invalid v0.

Rules use flat literal `match` and `constraint` maps. v0 intentionally has no expression language, no nested policy DSL, and no execution fields such as `auto_execute` or `execute_on_match`.

## Digest Binding

When this surface is active, `policy_digest` should hash the entire canonical policy surface artifact. `charter_digest` should hash the `charter_sections` subset. Both are SHA-256 digests over the repo's `magma-jcs-subset-v1` helper in `waggledance.core.magma.canonical`.

v0 does not add a signer, revocation mechanism, policy compiler, or gateway adapter. Those are later layers.

## Out Of Scope

- No Rego or Cedar evaluator.
- No MCP gateway policy enforcement.
- No runtime replacement for WriteRCOGate.
- No automatic external-effect execution.
- No multi-operator synchronization.
- No raw secret, credential, or payload fields.

## Smallest Useful Demo

The fixture at `tests/fixtures/policy_surface_v0.json` mirrors four WD risk classes and two charter sections. The contract tests validate the fixture, reject policy files that claim runtime authority, reject `external_effect` without operator approval, and prove the fixture digest can populate a MAGMA receipt `policy_digest`.
