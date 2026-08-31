# WaggleDance v4 profile and capability truth contract

> **TARGET-STATE SOURCE MATERIAL — NOT THE CURRENT RUNTIME.** These manifests
> define an acceptance contract for a future v4.0.0 release. They do not
> assert that v4.0.0 is shipped, do not mark a profile ready, and do not grant
> runtime, merge, release, Stage-2, `claim_safe`, or external-write authority.

## P0 boundary

P0 consists only of the two schemas under `schemas/v4_0_0/`, four manifests
under `configs/profiles/v4/`, this explanation, and their contract tests.
Neither application runtime nor release tooling loads these paths.
`runtime_wiring: false`, `contract_status: target_state_unwired`, and
`activation_authority: none` are mandatory. The P0 state schema fixes
`foundation_ready`, `profile_ready`, `claim_safe`, and receipt completeness to
false and excludes `READY` as a profile state. It therefore cannot be reused
as premature release evidence.

Changing that boundary is a later operator-explicit class-(a) wiring change.
It must add the semantic evaluator, probes, sealed evidence, and rollback
behavior together; changing a manifest alone does not grant authority.

The stable v4 profile set is exactly `HOME`, `COTTAGE`, `FACTORY`, and
`GADGET`. `APIARY` is excluded as a deployment profile. That does not delete
bee-domain capsules, solvers, aliases, or historical v3 policy data.

The current `WAGGLE_PROFILE` name is also used by unrelated solver-size
profiles. P0 neither resolves nor reads that variable. A later loader must
split the namespaces and reject missing, excluded, or unknown deployment
profiles without a default-profile or silent fallback.

## Closed inventories and states

All inventories are keyed maps, not lists. The schemas require every known key
and reject unknown keys, so a producer cannot hide a duplicate identifier in
a differently shaped list element. JSON and YAML are loaded with duplicate-key
rejection in the conformance suite.

The target inventory contains 26 v4 control-plane capabilities, 17 named
integrations, and 10 probes. The `catalog_crosswalk` independently covers all
27 IDs currently declared under `configs/capabilities/*.yaml`. A required
catalog item uses `target_kind: catalog_capability` and maps to its own exact
ID. It is not collapsed into a broader, unrelated control-plane capability.
The future state snapshot therefore has a distinct `catalog_capabilities`
observation for each source ID. An excluded source is explicitly
`migration_only` or `none`, with a rationale.

Each target is exactly one of:

- `required`: enabled by default in the eventual provisioned v4 target,
  release-required, and initially `UNPROVISIONED`;
- `not_applicable`: explicitly outside that profile, disabled, not a release
  requirement, initially `ABSENT`, dependency-free, and justified by a
  rationale.

The state vocabulary is closed: `ABSENT`, `UNPROVISIONED`, `STARTING`,
`READY`, `DEGRADED`, `BLOCKED`, `FAILED`, `STALE`, and `STOPPED`. Stable reason
codes are a closed, versioned registry using the `capability.`, `catalog.`,
`integration.`, `probe.`, and `profile.` namespaces. Unknown or ambiguous
observations must become `BLOCKED` in the later evaluator; they are never
coerced to ready.

Only a component in `READY` may serve. A failed required component keeps the
profile unready while the supervisor and diagnostic plane remain available.
Every `READY` component must carry an exact artifact digest, even though P0
still fixes profile readiness and authority false.
Legacy hologram labels such as `active`, `idle`, and `framework` do not map to
`READY` without the complete v4 proof.

## Profile integrations

| Profile | Required target integrations |
|---|---|
| HOME | MQTT, Home Assistant, Frigate, alerts, voice/audio, weather, electricity |
| COTTAGE | MQTT, weather, electricity, alerts, voice/audio, offline queue |
| FACTORY | MQTT, OTEL, Prometheus, Alertmanager, device enrollment, watchdogs |
| GADGET | MQTT, sensors, local model, signed OTA, power-loss recovery |

Every other known integration remains present as an explicit
`not_applicable` entry. All required integrations and probes start
`UNPROVISIONED`; target default-on is not present-tense enablement.

The common safety spine is redaction, credential non-exposure,
external-write gating, deny-by-default policy binding, and child/privacy
binding. It is required in all four profiles.

## Vector authority and GADGET

HOME, COTTAGE, and FACTORY target FAISS with `fallback: none`. Chroma is
`migration_only`: it may be opened only as a read-only migration source,
cannot serve, and cannot silently replace FAISS. Migration promotes only a
verified FAISS target. These declarations are dormant acceptance criteria;
they do not claim the current backend implementation is ready.

The approved 128 MiB GADGET target envelope has `backend: none`,
`chroma_mode: none`, and `migration_target: none`. Both
`retrieval.hybrid_faiss` and the outbound real-HTTP provider plane are
`ABSENT`; no Chroma migration tooling ships in that profile. GADGET still requires explicit
`unexpected_vector_backend_reachability` and
`unexpected_remote_provider_reachability` probes. A future conformance gate
must prove that neither forbidden plane is reachable; it may not skip the
profile. Local hot-cache and local Ollama reasoning remain separately tracked
catalog/integration requirements, including hallucination verification, and
are not mislabeled as FAISS or remote HTTP.

## Foundation, receipts, and `claim_safe`

Foundation readiness is separate from release readiness. Its exact set is:

- three evidence producers/verifiers: runtime receipts, chat-served receipts,
  and the receipt-chain verifier;
- the `audit.claim_safe_evaluator` (an evaluator, not the verdict itself);
- deterministic-solver-first routing;
- the five-item safety spine.

Runtime receipts, chat-served receipts, and receipt-chain verification remain
distinct evidence surfaces. A complete future window must bind one exact git
head, have equal served/runtime-covered/chat-covered counts, have zero gaps,
and provide both a chain-head digest and an evidence digest. P0 fixes the
window to incomplete.

The later derived `claim_safe` verdict may become true only after foundation
readiness, complete receipts, exact configuration binding, and exact artifact
binding. Release capabilities marked `activation_requires_claim_safe` remain
unservable until that verdict is true. Profile readiness then additionally
requires every release item. This ordering avoids making the evaluator depend
on the verdict it computes.

P0 does not add `/capabilities`, change `/readyz`, or modify `/healthz`.
Future endpoint wiring must expose stable reason codes without raw exceptions,
keep liveness separate from readiness, and preserve diagnostics after a
component failure.

## Digest definitions

All digests are lowercase SHA-256 hex:

- manifest digest: `sha256(exact UTF-8 file bytes)`;
- configuration digest: `sha256(canonical_json_rfc8785(effective_config))`;
- snapshot digest when one is embedded in later evidence:
  `sha256(canonical_json_rfc8785(snapshot))`;
- artifact digest: `sha256(exact artifact bytes)`;
- source binding: the exact 40-character git head used by the producer.

No platform newline normalization, YAML reserialization, unordered JSON dump,
latest-file selection, or filename inference is part of a valid preimage.
Later producers must reject ambiguous candidates instead of choosing one.

## Governance, rollout, and rollback

This eight-file slice is an off-allowlist PR. It does not modify the charter
or a verdict-computing path, but the new truth contract is still
operator-explicit under current governance. Conformance, RCO, or CI success
does not grant its merge.

The accepted sequence after P0 is: cause-B veto latch; dormant Rule 9b broker;
signed policy/evidence closure; one real PR-cycle soak; a narrow expiring
grant; sealed evidence runtime; backend conformance and locks; read-only
Chroma-to-FAISS migration; and only then product/profile activation. A later
runtime-wiring PR must reproduce exact-head review because it changes dormant
material into authority-bearing behavior.

Before wiring, rollback is a normal PR revert and has no runtime data effect.
After wiring, each capability requires a separately tested local block,
recovery, and evidence path. Dirty worktrees, unsealed artifacts, missing
observations, stale heads, or digest disagreement always fail closed.
