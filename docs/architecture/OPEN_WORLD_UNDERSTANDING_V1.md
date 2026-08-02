# Open-World Understanding V1

Status: source-only, default OFF, explicit shadow mode only.

This document separates the implemented slice from the WaggleDance target
state. It does not grant merge, deployment, runtime activation, routing,
action, builder, promotion, Dream, replica-write, or hive-commit authority.

## Outcome and boundary

Open-World Understanding V1 adds an auditable local learning side channel to
`AutonomyRuntime.ingest_sensor_observation`. When explicitly configured as
`shadow`, the side channel predicts before the observed numeric value is
revealed, verifies the reveal, records the result in a hash-chained MAGMA
ledger, and updates only reversible local shadow state.

The shipped configuration is literal `mode: "off"`. The OFF path returns
before it imports the implementation or constructs a SQLite ledger. The only
accepted opt-in value is literal `shadow`; unknown modes fail closed.

The current live seam is the direct Python ingestion API. No MQTT, Home
Assistant, fusion, router, action bus, BuilderHost, model promotion, Dream
Mode, replica transport, or automatic subdivision adapter is enabled by this
work.

## Implemented data path

```text
value-free observation header
        |
        v
commit prediction against prior state
        |
        v
durable prediction_committed event
        |
        v
reveal value + secret nonce, verify original commitment
        |
        v
classify residual and append one atomic resolution batch
        |
        v
reversible local state + raw-free public projection
        |
        v
optional curiosity / knowledge-delta proposal (still inert)
```

The predictor receives neither `value`, arbitrary metadata, nor the
secret-until-reveal nonce. Public and synthetic observations use a fresh
128-bit nonce as well: their pre-reveal commitment is an ordering primitive,
not a low-entropy dictionary oracle. Private and restricted observations are
blocked from this V1 shadow learner even when a local HMAC key is available.

Issued tickets are detached and fingerprinted. A foreign, modified, replayed,
or process-old ticket cannot update state. Prediction and resolution batches
are idempotent and atomic; state changes happen only after the sink confirms
the complete append.

The pre-reveal `source_sequence_identity_digest` is derived only from stable
public source-sequence fields. It deliberately excludes both the raw numeric
value and the opaque metadata digest: either would make low-entropy content a
dictionary oracle or make identical source retries look different. Metadata
itself is represented by a freshly salted commitment whose salt is never
persisted or revealed. Reusing one source sequence with the same public header
is therefore suppressed as a duplicate, regardless of newly supplied value or
metadata, and cannot update state; changing a public header field under the
same sequence triggers quarantine. Custom predictors must supply explicit
artifact and configuration digests, so a different predictor cannot silently
inherit LastValue state.

## MAGMA memory and restart

`UnderstandingLedger` is a local SQLite WAL/FULL append-only event sink. It
uses exact event allowlists, an event hash chain, exact batch receipts,
idempotency keys, append-only triggers, explicit read transactions, and
startup verification of schema and trigger definitions. Corrupt receipts,
missing receipt coverage, replaced triggers, sequence overflow, torn chains,
or semantic replay contradictions fail closed.

Every prediction also persists a `learning_policy_digest` and a verified
`learning_domain_digest`. The latter binds the full policy commitment,
source/modality/unit, predictor artifact and configuration, prediction TTL,
fixed update algorithm, and the complete cell address including incarnation,
generation, and fence. Replay rejects mixed domains, and restart rejects a
configured domain that differs from the ledger. A policy, unit, predictor, or
cell-fence change therefore requires an explicit migration or a new ledger; it
cannot reinterpret old numeric state.

The normal `UnderstandingProjectionV1` contains commitments and lifecycle
summaries but excludes revealed values, nonces, prediction values, residuals,
and learned numeric values. Nested projection records are deeply frozen and
all authority fields serialize as literal `false`.

A separate process-local restart reducer may contain numeric state. It has no
serializer, digest, network, routing, action, promotion, or authority API.
After verified replay it restores numeric states, sequence high-watermarks,
and the trusted-time watermark. A prediction whose secret reveal context died
with the process is atomically recorded as
`expired/restart_lost_reveal_context`; the old ticket is never reissued. That
reconciliation is a compare-and-append transaction pinned to the replayed
ledger head. If an old live process wins the race, restart writes nothing,
replays the winning head, and retries only within a fixed bound. The recovered
loop then pins that verified head and compare-and-appends every later durable
batch; a stale restarted instance cannot extend a ledger advanced by another
writer. A revealed but unresolved lifecycle is impossible on the normal atomic
writer and therefore blocks restart rather than being guessed through. Pending live-process
predictions also have a bounded default lifetime of 300 seconds. The exact TTL
is persisted in the learning domain; semantic replay requires a TTL expiry to
occur strictly after its deadline and every disposition to occur no earlier
than its prediction. Expiry is durably recorded before reveal and all stale
tickets are swept before a new prediction can consume the pending-capacity
gate. Clock rollback fails before it can pin a resolution timestamp or append
false temporal evidence.

The shipped relative ledger path is resolved under the persistent project
root, independent of the process working directory, and `..` escape is
refused. Absolute operator-configured paths remain explicit. A malformed
`open_world_understanding` YAML scalar/list fails closed instead of silently
behaving as OFF. If later container construction fails after the loop exists,
the loop and SQLite handle are closed before the original error is re-raised.

## The bee protocol as code

The mapping to the public
[WaggleDance architecture description](https://www.rakentaja.org/waggledance/fi/)
is concrete but deliberately incomplete:

| Bee mechanism | V1 code mechanism | Current limit |
|---|---|---|
| scout dance | digest-bound knowledge proposal | offline shadow proposal only |
| direction, distance, quality | proposal identity, evidence, confidence, expiry | no production route change |
| antenna feedback | authenticated SUPPORT and CHALLENGE | offline evaluator only |
| stop signal | authenticated STOP veto | no transport or live hive gate |
| honeycomb memory | append-only MAGMA ledger and replay | one local modality/cell |
| replacement after cell death | authenticated exact 2-of-3 checkpoint selection | plan only; no replica writes |
| night learning | future counterfactual replay over verified failures | Dream integration deferred |

This slice improves the ideology by making prediction ordering, challenge,
STOP, provenance, and restart behavior falsifiable in code. It does not prove
the broader claims that every runtime path is solver-first, every decision is
already in this ledger, or Dream Mode autonomously improves this learner.

## What “hexagonal” means

A true axial ring-1 consists of one center and six coordinate neighbors. A UI
may show eight logical domains, but the number eight alone does not establish
hex geometry. V1 validates all six axial neighbors around a center and binds
each cell to:

- logical `cell_id`;
- axial `(q, r)` coordinate;
- `incarnation_id`;
- monotonically advanced `generation` and `fence`.

The reusable target cell template is:

```text
Cell
|- bounded observation policy
|- prediction-before-reveal loop
|- append-only ledger
|- pure public projection + process-local restart reducer
|- ring-1 WDP evaluator
`- three-replica recovery evidence contract
```

Every future child cell must use the same template. Parent-child hierarchy is
an explicit edge separate from axial neighbor edges. A subdivision operator
must create a new fenced child registry and budgets; it must not merely copy a
Python object or reuse a parent's keys.

The current ledger is intentionally single-domain. Rebuilding or subdividing
a cell changes its learning-domain digest, so the old ledger cannot be opened
as if it belonged to the new fence. Live recovery must create an explicit
verified migration/new-ledger boundary rather than mutating that invariant.

Implemented now:

- exact axial ring-1 validation;
- current-incarnation/generation/fence checks;
- authenticated proposal and signal envelopes;
- STOP/CHALLENGE before support approval;
- bounded signal counts, TTLs, and revision depth;
- non-authoritative recovery selection and a replacement address above every
  authenticated observed generation/fence.

Still required for a cell to die and rebuild in a running system:

- a failure detector with false-positive and partition policy;
- three durable replica writers in distinct attested failure domains;
- checkpoint creation bound to verified ledger and projection heads;
- externally pinned trust-registry and Genesis roots;
- transport authentication and replay protection;
- an orchestrator that fences the old incarnation before materialization;
- crash tests covering death during append, checkpoint, selection, and
  materialization;
- a parent/child subdivision scheduler and resource accounting.

Until those exist, “recoverable hex cell” means a verified offline selection
and rebuild plan, not an autonomous live replacement.

## Genesis, independence, and echo chambers

A SHA-256 digest proves byte identity or difference. It does not prove that
two reviewers are independent. Different hashes can still represent the same
code family, model provider, prompt policy, toolchain, data source, operator,
or physical host. Conversely, identical evidence bytes do not automatically
make two genuinely different methods equivalent.

V1 therefore derives quorum facts from a digest-bound trusted registry, not
from reviewer self-description. Independence requires an orthogonal pair on
all current axes:

- Genesis root lineage;
- verifier/model/prompt/toolchain method group;
- evidence-root group;
- physical failure domain.

Duplicate key metadata and different key IDs resolving to the same key bytes
are rejected. Resolver callbacks operate only after a detached registry and
input snapshot exists, so they cannot mutate trust facts after digest
validation. Invalid envelopes cannot erase an authenticated STOP or CHALLENGE.

Genesis is the externally pinned origin of an identity and its lineage. A
self-minted registry is not trust merely because it hashes correctly. The
integration layer must pin the expected registry digest outside the proposal
and signal inputs. Central HMAC is acceptable only for this one-host shadow
evaluator. Distributed or authoritative WDP requires asymmetric signatures,
key rotation/revocation, attestation, and preferably Ed25519-class identity
keys before any cross-host vote has standing.

Future anti-echo aggregation should count distinct method × evidence ×
failure-domain cohorts, cap the fraction contributed by one cohort, retain
minority counterevidence, and make silence or an unretracted STOP block by
default. Raw support count and “different SHA” are not quorum metrics.

## How local learning can grow total capability

The scalable knowledge path is:

```text
local observation
  -> prior prediction
  -> verified counterexample
  -> local reversible update
  -> raw-free knowledge delta
  -> independent WDP review
  -> human-gated hive projection
  -> retrievable evidence for every cell
```

Only the first four steps and inert proposal generation are present in this
slice. There is no hive commit. Later cells should consume committed knowledge
through a versioned projection, never by copying another cell's mutable
working state. Retraction, expiry, lineage, and provenance remain attached so
bad learning can be removed and replayed.

To “learn everything it observes” safely, the system must mean broad coverage,
not unbounded retention or universal understanding. The repeatable learning
cycle should be:

1. classify the observation's schema, privacy, source, and cost;
2. select or create a bounded modality cell from an approved template;
3. make a falsifiable prediction before reveal;
4. measure surprise and calibration, not merely task success;
5. schedule bounded curiosity for high information value;
6. propose a hypothesis with counterevidence and expiry;
7. test it on withheld or independently sourced evidence;
8. retain, retract, merge, or forget it through MAGMA replay.

Budgets must exist for sources, targets, tickets, sequences, curiosity,
revisions, ledger retention, compute, and energy. Unknown schemas should form
audited gaps, not automatically trigger code execution or an LLM call.

## Solver construction and the 50,000-solver question

V1 capability gaps are intentionally inert:

- `solver_build_eligible` is always `false`;
- `builder_invoked` is always `false`;
- no BuilderHost call exists in the understanding loop.

BuilderHost should never hand-write 50,000 bespoke solver source trees. The
scalable model is:

```text
small set of reviewed solver families
  × versioned code/template artifact
  × declarative schema/config/parameters
  × many registered instances
```

Most of 50,000 “solvers” should be cheap descriptors that share code,
sandbox images, tests, and caches. BuilderHost is reserved for a genuinely new
family or missing primitive, after deduplication shows an existing family and
configuration cannot cover the gap.

A future coding sandbox needs:

- a fresh, disposable workspace and immutable base image;
- no network by default and explicit capability tokens;
- read-only inputs and narrow output directories;
- CPU, memory, disk, process, and wall-clock quotas;
- dependency allowlists, lockfiles, SBOM, license and secret scanning;
- deterministic compile, unit, property, fuzz, and adversarial tests;
- artifact, prompt, model, toolchain, and test-result digests in MAGMA;
- candidate-only registration followed by canary and human/RCO promotion.

Generated code never receives runtime secrets or production write access. A
candidate may fail, time out, or be discarded without changing the active
solver registry.

## Fresh Grok advisory

The operator-requested provider-default Grok CLI review was run without a
model pin and with read/search-only tools. The durable runtime guide reported
`grok-4.5` as the observed provider default; that observation is evidence only.

The CLI could inspect the pushed C2 commit but reported that it could not read
the local uncommitted C3 tree. It therefore correctly classified ledger, WDP,
and recovery as unverified at review time. Its main recommendations were to
avoid claim/code drift, keep caches and retention bounded, prove restart by
pure replay, enforce lineage rather than support counts, retain STOP as an
absolute veto, and represent large solver populations as families/templates
plus configuration. Those recommendations shaped C3. They are not a vote,
promotion, or authority grant, and Grok did not review the final C3 commit.

## Two-week sprint ledger

| Slice | Deliverable | Status |
|---|---|---|
| C1 | frozen contracts, privacy, lineage, hard-false authority | pushed |
| C2 | bounded two-phase loop, secret nonce, atomic runtime seam | pushed |
| C3 | durable ledger, replay/restart, authenticated WDP/recovery | pushed |
| C4 | default-OFF wiring, semantic-domain guard, docs, executable harness | implemented; local selector gate green |
| Gate | exact pushed-head RCO1/RCO2/Fable review and CI | pending |

Parallel lane intent:

- lead: contracts, runtime, ledger, projection, WDP, recovery, wiring, docs;
- Tools: independent C3 contract slice (12 tests); its managed sandbox could
  not write the harness files, so it released the clean scope and lead
  implemented the executable harness under a new bounded claim;
- RCO1: security, trust, privacy, sandbox, and authority boundary;
- RCO2: causal ordering, replay, fencing, false consensus, and Goodhart risks;
- Fable: architectural invariants, public-ideology fit, and claim/code parity;
- Grok: provider-default advisory only.

The local harness is executable with:

```text
python tools/run_open_world_understanding_v1.py --json
```

It uses only disposable synthetic state and checks prediction privacy,
value-independent pre-reveal identity, salted opaque metadata, nonce
freshness, restart hydration and one-time pending expiry, tamper rejection,
raw-free projection, semantic-domain binding, authenticated STOP, shared-key
quorum refusal, revision bounds, stale-fence recovery refusal, fenced rebuild
planning, direct object state, raw ledger events, and literal-false authority
flags. Its JSON is aggregate evidence, not a claim-safe or activation artifact.
The `external_writes_applied: false` gate means no product or external-system
write was performed; the harness necessarily writes its disposable local
SQLite database and, when requested, the explicit local `--out` report.

## Exit criteria before any wider activation

Shadow activation remains a separate operator decision. Before even a narrow
synthetic live adapter is considered, require:

- exact pushed head and green CI;
- independent harness and dual RCO review;
- explicit retention/redaction policy for reveal events;
- ledger rotation/archive design without breaking replay;
- metrics for audit gaps, pending expiry, quarantine, budget drops,
  calibration, and STOP/challenge outcomes;
- restart and corruption drills on the deployment filesystem;
- no private/restricted raw-value path;
- proof that every authority flag remains false.

Before routing, action, builder, promotion, Dream, live recovery, or hive
commit authority, require a new scoped design and explicit authorization. None
of those powers is implied by this V1 implementation.
