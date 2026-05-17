# MAGMA Substrate Audit - 2026-05-17

Status: operator-visible audit report.

This report summarizes the WD V12 thin spine that exists after the May 2026
MAGMA, policy surface, and idle protocol hardening work. It is intentionally
claim-conservative: it records what the substrate can prove today, what remains
unproven, and which next slices improve strategic differentiation without
expanding the external-effect authority path.

Reference head at time of writing: `2b6672d feat(magma): cross-validate policy
surface digests (#456)`.

## Executive Summary

WD is now more than a write-action gate, but it is not yet a fleet-learning
substrate. The current proven core is:

1. A MAGMA receipt v1 envelope that binds payload, policy, charter, and
   EvaluationResult digests when those artifacts are supplied, and requires
   caller-provided digest fields for RCO decision, world snapshot, and solver
   contract references.
2. A canonical EvaluationResult v0 contract for verifier and promotion
   evidence.
3. An offline verifier that detects manifest-shape, payload, evaluation-result,
   chain-topology, and optional policy-surface binding failures.
4. A Policy Surface v0 declarative mirror whose digests can be bound into
   receipts while preserving the existing charter-as-code authority path.
5. An opt-in Idle Protocol v1 path for strategic two-agent deliberation when no
   implementation PR exists, with no auto-execute path.

The strategic shape is therefore:

> models may propose capability growth, but authority remains in policy,
> receipts, replayable evidence, RCO decisions, and operator-owned promotion
> gates.

## Evidence Inventory

This report treats a claim as proven only when it has a repo artifact and local
test or verifier evidence. The current audit spine is:

| Area | Artifact | Evidence |
| --- | --- | --- |
| Canonical digest helper and receipt verification | `waggledance/core/magma/canonical.py`, `tools/verify_magma_receipt.py` | `tests/tools/test_verify_magma_receipt.py` |
| MAGMA receipt v1 schema | `schemas/v3_13_0/magma_receipt.v1.json` | `tests/contracts/test_magma_receipt_schemas.py` |
| EvaluationResult v0 schema | `schemas/v3_13_0/evaluation_result.v0.json` | `tests/contracts/test_magma_receipt_schemas.py` |
| Receipt emitter helper | `waggledance/core/magma/receipt.py` | `tests/unit/test_magma_receipt_emitter.py` |
| Policy Surface v0 | `schemas/v3_13_0/policy_surface.v0.json`, `docs/architecture/POLICY_SURFACE_V0.md` | `tests/contracts/test_policy_surface_schema.py` |
| Synthetic adversarial corpus v0 | `schemas/v3_13_0/synthetic_adversarial_case.v0.json`, `schemas/v3_13_0/synthetic_adversarial_expectation.v0.json` | `tests/tools/test_validate_synthetic_adversarial_corpus.py` |
| Idle Protocol v1 | `schemas/v3_13_0/idle_protocol.v1.json`, `docs/architecture/IDLE_PROTOCOL_V1.md` | `tests/tools/test_idle_protocol_activate.py`, `tests/unit/test_idle_protocol_validator.py`, `tests/unit/test_idle_protocol_convergence.py` |

The PR sequence that produced this spine is #449 through #456 class work plus
the adjacent synthetic-corpus and PDAM EvaluationResult demo commits. The exact
local verification for this report was:

- MAGMA, policy, and idle related tests: 87 passed.
- Bridge event validation: 2812/2812 valid before this report's RCO event.
- Savepoint focused verifier tests: 18 passed.

## What Is Proven

### MAGMA Receipt v1

`schemas/v3_13_0/magma_receipt.v1.json` defines a tamper-evident envelope for
per-action audit evidence. The schema requires digest fields for:

- `canonical_payload_digest`
- `prev_receipt_hash`
- `policy_digest`
- `charter_digest`
- `rco_decision_digest`
- `world_snapshot_digest`
- `solver_contract_digest`
- `evaluation_result_digest`
- `approval_id`
- `operator_gate_required`

For `external_effect`, `operator_gate_required=true` is schema-enforced.

Signing fields are present, but v1 does not require a signing dependency:
`signature_algorithm`, `signature`, and `key_id` may all be null together.

Important limit: `rco_decision_digest`, `world_snapshot_digest`, and
`solver_contract_digest` are opaque caller-provided digest fields today. The
receipt schema can require their presence and shape; the current verifier does
not recompute those underlying external artifacts.

### EvaluationResult v0

`schemas/v3_13_0/evaluation_result.v0.json` gives the counterfactual,
adversarial, promotion, and solver-review paths one canonical result shape.
It includes target binding, risk class, expected and actual gates, verifier
path, solver selection, policy and threshold versions, verdict, confidence, and
typed uncertainty sources.

This matters because a receipt alone proves that an event existed; an
EvaluationResult records how that event scored against the relevant gate,
policy, or solver expectation.

### Offline Receipt Verifier

`tools/verify_magma_receipt.py` verifies receipt manifests locally. The current
tool checks:

- manifest shape and receipt schema validity;
- payload digest binding;
- EvaluationResult digest binding;
- previous-receipt chain topology;
- optional expected policy and charter digests;
- optional `--policy-surface` binding against a concrete Policy Surface v0
  artifact.

The verifier is Profile S compatible: it is local, file-based, and has no
network dependency.

The verifier has an explicit unsigned-v1 limit: it detects in-chain receipt
tampering, but tail receipt field tamper needs either a successor receipt or a
future signature verifier.

### Policy Surface v0 Binding

`docs/architecture/POLICY_SURFACE_V0.md` and
`schemas/v3_13_0/policy_surface.v0.json` define a declarative audit mirror,
not a runtime authority transfer.

The schema requires:

- `authority=declarative_audit_only_v0`
- `authority_mode=declarative_mirror`
- `kernel_authoritative=true`
- `no_runtime_enforcement_change=true`
- `no_auto_execute_grant=true`
- `external_effect_operator_gate=true`
- `offline_profile_s_supported=true`

PR #456 closed the audit loop by adding verifier support for:

- `policy_digest = sha256_digest(policy_surface)`
- `charter_digest = sha256_digest(policy_surface["charter_sections"])`

This proves that a receipt chain can be checked against one concrete
operator-readable policy artifact. It does not make that artifact a runtime
policy engine.

### Synthetic Adversarial Corpus v0

The synthetic adversarial corpus adds typed, local cases for early reviewer and
solver-evaluation pressure. Its important design choice is separating
reviewer-visible cases from expectation fixtures, which supports review-blind
workflows when reviewer tooling loads only the cases. The repository layout
does not enforce blindness by itself because the expectation fixtures are still
available locally to validator and evaluator tooling.

The corpus is a substrate input, not a production evaluator by itself. It
becomes strategically important when paired with historical replay and
EvaluationResult emission.

### Idle Protocol v1

`docs/architecture/IDLE_PROTOCOL_V1.md` records the current idle-deliberation
primitive:

- `tools/idle_check.py` reports idle only when all quiet-window predicates hold.
- `schemas/v3_13_0/idle_protocol.v1.json` defines proposal, counter-proposal,
  adversarial review, consensus, low-quality, and charter-violation payloads.
- `waggledance/core/idle_protocol.py` validates quality and convergence.
- `tools/idle_protocol_activate.py` emits only when manually invoked with
  `--apply` or `--emit`; dry-run is the default.

The current idle activation path is opt-in and manual-apply gated. Consensus
reports keep `operator_gate_required=true` and `auto_execute=false`; ordinary
idle emissions are controlled by explicit `--apply` or `--emit`, idle/prior
event checks, and payload validation rather than a separate approval service.
Consensus does not become implementation work automatically.

## What Is Not Proven

The current substrate should not be described as having any of these properties
yet:

- no Ed25519 or ML-DSA signature verification;
- no key rotation, revocation, public verify endpoint, or RFC3161 anchoring;
- no runtime policy evaluator, Rego/Cedar export, or policy authority transfer;
- no multi-instance replay exchange or fleet-learning network;
- no cross-instance sanitization contract;
- no automatic consensus-to-scout conversion from idle protocol output;
- no automatic solver promotion to live traffic;
- no operator-out-of-loop path for `external_effect`;
- no enforced reviewer blindness for synthetic corpus users who can read both
  case and expectation fixtures;
- no proof that the synthetic corpus predicts real production failure rates.

These are deliberate exclusions. They keep v1 local-first, auditable, and
charter-aligned while the receipt and evaluation spine matures.

## In Question

These are known uncertainties or follow-up observations, not current blockers:

- `tools/verify_magma_receipt.py` still has tool-local schema validation
  reporting. The current tests protect known privacy-canary paths, but a shared
  redacted schema-error helper would make the pattern consistent across MAGMA
  CLIs.
- The bridge-consensus loop has been operationally useful across the May 2026
  hardening PRs, but it is not a formal guarantee. It should be measured with
  catch-rate, disagreement, latency, and post-merge-regression metrics before it
  is described as a proven governance model.
- Policy Surface v0 is intentionally a declarative mirror. A future authority
  transfer needs its own RFC, tests, and receipt digest semantics.
- The synthetic adversarial corpus provides pressure cases. It does not yet
  estimate real-world production failure probability or correlated-review risk
  without replay and observed reviewer outcomes.

## Residual Risks

### 1. Verifier Claims Can Outrun Runtime Integration

The verifier proves fixture and manifest integrity. It does not yet prove that
every production action emits a receipt through the same path. Until runtime
emitters are wired everywhere, documentation must say "verifiable artifact" and
not "complete production provenance".

Smallest fix: add runtime receipt-emission coverage one solver path at a time,
starting with the already-demonstrated PDAM EvaluationResult path.

### 2. Unsigned Tail Receipts Remain Mutable

Hash chaining detects tampering once a successor receipt commits the prior
hash. The last receipt in an unsigned chain still depends on filesystem and
operator process controls.

Smallest fix: add a signature-envelope verifier scaffold with local dev keys
and fixtures before adding public verification or external anchoring.

### 3. Policy Surface Is Easy To Overclaim

Policy Surface v0 is an audit mirror. It is not the runtime source of refusal,
approval, or execution authority. Treating it as runtime policy before a formal
compiler or evaluator exists would create a docs-vs-behavior mismatch.

Smallest fix: keep `authority=declarative_audit_only_v0` pinned until a separate
policy-authority RFC is accepted and tested.

### 4. Diagnostic Redaction Must Stay Default

Receipt, policy, and adversarial-corpus tools can be run in CI where logs
persist. Error messages must remain path/count/status oriented and avoid echoing
payload contents, privacy canaries, operator data, or raw policy text.

Smallest fix: factor the redacted schema-error pattern into a shared helper and
use it in every MAGMA validation CLI.

### 5. Single-Operator Governance Tension Remains

The current alpha loop is productive because one operator owns charter
interpretation, release holds, and domain curation. That is acceptable for
speed, but it is not the final decentralization shape.

Smallest fix: record an `independent_reviewer_v0` role as read-only plus
comment plus charter-tension raise, without live veto power in alpha.

## Next Three Thin Slices

### 1. Runtime Receipt Emission For One Concrete Solver Path

Scope: connect the existing MAGMA receipt and EvaluationResult helpers to one
real solver flow, preferably PDAM, and verify that the emitted manifest passes
`verify_magma_receipt.py --policy-surface`.

Why first: it closes the biggest overclaim risk. The substrate already has
schemas, fixtures, and a verifier; one live path proves the spine is usable
outside tests.

Non-goals: no multi-instance export, no new signer, no policy authority
transfer.

### 2. Counterfactual Replay Demo Bound To EvaluationResult

Scope: replay one historical external-effect-style case through three variants:
policy v1, policy v2, and one domain-threshold change. Emit an
EvaluationResult for each and show the diff in expected/actual gate, solver
selection, verifier path, risk class, verdict, and final outcome. A
human-readable RCO decision diff requires a paired RCO artifact; the current
EvaluationResult v0 schema only binds RCO evidence indirectly through the
receipt's `rco_decision_digest`.

Why second: this is the concrete demo that separates MAGMA from a generic audit
log. It shows how WD can reason about capability changes before promotion.

Non-goals: no automatic promotion, no production traffic allocation.

### 3. Sanitized Export Bundle Contract

Scope: define a local-only export artifact for future multi-instance replay:
receipt digest, policy digest, charter digest, EvaluationResult digest,
redaction level, and allowed visibility. Keep raw payload sharing out of v1.

Why third: multi-instance replay should not start before the sanitization shape
exists. A contract can be reviewed and tested locally before any pilot exchange.

Non-goals: no network protocol, no multi-operator pilot, no public verify
service.

## Glossary

- `magma-jcs-subset-v1`: the local canonicalization contract used by the MAGMA
  digest helper. This report does not claim full external RFC 8785 coverage.
- `sha256_digest`: the shared canonical digest helper used by receipt, policy,
  and verifier tests.
- `EvaluationResult v0`: the canonical output shape for solver, policy,
  counterfactual, promotion, and peer-review evidence.
- `declarative_audit_only_v0`: the Policy Surface authority constant meaning
  "audit mirror only, no runtime enforcement transfer".
- `declarative_mirror`: the Policy Surface v0 authority mode. Runtime refusal
  and approval still remain in the charter-as-code kernel.
- `external_effect`: the highest WD risk class in this audit. Receipts and
  policy fixtures require the operator gate for this class.
- `operator_gate_required`: the receipt-level flag that must be true for
  `external_effect` receipts.
- `idle_protocol.v1`: the bridge deliberation payload contract for strategic
  design discussion when implementation flow is idle.

## Audit Position

The current WD differentiator is no longer "we have a write-action gate." That
surface is commoditizing. The defensible direction is:

> WD is a verifiable solver-growth substrate: candidate capability grows in
> shadow, is scored through canonical EvaluationResult artifacts, is bound into
> MAGMA receipts, is checked by offline verifiers, and reaches live authority
> only through explicit operator gates.

As of this report, WD has the first half of that sentence in working substrate
form. The second half still needs runtime emission, counterfactual replay, and
sanitized export contracts before it should be claimed as production-ready.
