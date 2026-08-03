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

### Plaintext retention truth

The raw-free public projection is not an at-rest redaction claim. V1 retains
plaintext numeric reveal material plus reconstructive residual and numeric
state fields indefinitely in the local SQLite database; the same material may
also remain in WAL pages until SQLite checkpoints them. Full semantic replay
can therefore reopen the commitment and independently verify the state
transition. Removing only `observation_revealed.value` would be cosmetic: the
same value can be reconstructed from the prediction plus residual and from
adjacent EWMA states.

Entering literal `shadow` therefore also requires the exact configuration
acknowledgement
`retain_plaintext_local_for_full_semantic_replay_v1`. Missing, misspelled,
`ttl`, `redact`, or `encrypt` claims fail before ledger construction. The
policy is included in the complete learning-policy commitment, so a ledger
created under another policy cannot silently inherit the state.

The shipped OFF configuration intentionally contains only a commented example
of that literal. Changing only `mode` to `shadow` therefore fails closed; an
operator must add the acknowledgement in the activation configuration. The
lower-level loop/policy constructors retain a fixed V1 schema default for
tests and the acceptance harness, but they are not an activation boundary or
an authority path. Production construction goes through the container gate.

`UnderstandingLoop.retention_truth()` first requires a successful semantic
replay, computes the raw-free status from the resulting public projection, and
then exposes only aggregate facts: raw reveal count, the oldest matched
resolution timestamp and age, timestamp coverage, whether the sink is the
durable verified local ledger, the literal policy, and hard-false
deletion/encryption and authority flags. It distinguishes the V1 schema's
retention contract from whether reveal material is actually present. It never
returns a value, residual, nonce, state, or ledger path.

This is an honest risk acknowledgement, not bounded retention and not an exit
gate closure. Irreversible erasure is incompatible with replaying the erased
prefix from genesis. A future V2 must use an approved library AEAD, an audited
segment-key provider with durable destruction receipts, authenticated
checkpoint anchoring, and an explicit `redacted prefix + full replay after
checkpoint` coverage mode. Home-grown encryption, hash/HMAC “redaction” of
low-entropy values, SQLite row deletion/rehashing, or unauthenticated
checkpoints are refused designs.

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

The local regression suite also kills a separately spawned ledger process at
two deterministic barriers: after both event rows and the batch receipt are
staged but before SQLite executes `COMMIT`, and after `COMMIT` returns in the
worker but before a modeled application result is delivered. At those exact
boundaries, reopen demonstrates pre-commit rollback, post-commit persistence,
verified-chain and receipt integrity, exact idempotent retry after a modeled
lost result, and refusal of mismatched reuse. It does not kill SQLite during
WAL-frame writing, sync, checkpoint, or partial I/O, and it does not simulate
sudden power loss or storage-controller lies.

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
model pin and with read/search-only tools. Historical model labels are not a
runtime pin or current identity claim.

An early review could inspect only pushed C2 and correctly left the then-local
C3 unverified. A later provider-default exact-head advisory inspected pushed
head `3f443301` and classified it as a strong, honest fail-closed shadow
skeleton rather than a live hive. It highlighted plaintext reveal retention,
external Genesis/registry pinning, cross-host asymmetric trust, live recovery,
calibration/Goodhart metrics, and solver-family scaling as the main remaining
risks. The retention-truth gate and process-crash drill in the V1.1
continuation address disclosure and crash evidence, not encryption, live
recovery, or activation.

A separate Grok-scout V12 review recommends paired incumbent-versus-candidate
lift on held-out cases before solver promotion. C6 now provides the first
bounded evidence contract for that recommendation in
`waggledance/core/learning/understanding_paired_evaluator.py`:

- the evaluator is default-OFF and has no container, runtime, registry, ledger,
  routing, BuilderHost, or promotion wiring;
- one immutable plan commits to distinct candidate/incumbent artifacts, a
  solver family plus hex cell/subdivision address, a pinned registry snapshot,
  an asserted sealed holdout, selection and arm-order policies,
  arm-independent-oracle assertions, runner/toolchain/environment/resource
  policy, and one candidate attempt for that supplied campaign;
- the evaluator recomputes the plan's manifest digest from each submitted
  `(case commitment, declared-unit commitment)` pair, so post-plan membership
  substitution is inconclusive rather than a positive result;
- shadow evaluation accepts at most 4096 pre-scored pairs, requires at least 20
  unique case and caller-declared unit commitments, preserves solver
  timeout/error outcomes in the denominator, and makes missing, mutated,
  mis-bound, or duplicated pairs inconclusive. Unit-commitment uniqueness does
  not establish statistical independence or an effective sample size;
- a positive leakage-audit assertion and its digest are required for a positive
  delta label, but this accountant does not execute or authenticate that audit;
- the deterministic receipt contains only artifact/evidence digests and
  aggregate pass/failure/reported-delta counts. It requires externally asserted
  HMAC-form case and evidence commitments, but validates only their shape; it
  does not prove MAC authenticity or key custody;
- positive, zero, and negative reported pass deltas are distinguished, but the
  receipt hard-codes runtime/routing/promotion/registry/builder/external writes
  false and also records receipt origin, same-input execution, leakage,
  artifact-to-family/cell binding, statistical-unit independence, and
  cross-campaign multiplicity as not independently verified.

This proves only deterministic aggregate accounting for submitted, pre-scored
rows whose opaque case/unit manifest matches the supplied plan. It does not
prove that the rows were actually held out. Digest commitments do not
authenticate receipt origin or independently prove temporal sealing, oracle
independence, same-input execution, leakage isolation, or single-attempt
selection. Attempt index/budget `1` has no cross-campaign memory and therefore
cannot prevent repeated trials or candidate search. Those controls still need
an isolated, authenticated runner and externally pinned evidence. This module
is not a runner and neither invokes nor supersedes `run_shadow_evaluation` or
`compute_counterfactual_delta`; it is intentionally not wired into the existing
fail-open counterfactual summary or `AutoPromotionEngine`. All Grok outputs
remain advisory rather than votes or gates.

A fresh provider-default advisory then inspected exact pushed C7 head
`fc4474b00fe898379ea8ef7679ef04ea488f8417` without a model pin. It ranked a
pure, non-executing capability contract ahead of either a subprocess executor
or free-form generated-code execution on the current Windows CI substrate and
returned `NARROW`: do not claim a sandbox before its controls can be exercised.
Separate API and security review lanes narrowed the implementable extension one
step further: a fixed controller-selected worker in the local trusted computing
base (TCB) may parse, compile, and package bounded
source while the candidate remains inert. This is useful only if it is named a
static preflight and every sandbox, safety, correctness, and promotion claim
stays false. The Grok result and the other reviews are advisory; none is a vote
or authorization.

### C7 closed paired execution

C7 adds the next isolated step in
`waggledance/core/learning/understanding_paired_runner.py`. It turns one
precommitted C6 plan and a private canonical-JSON case pack into C6
observations, then immediately returns an aggregate C7 receipt containing the
aggregate C6 receipt. It remains default-OFF and has no container, settings,
ledger, registry, router, BuilderHost, Dream, promotion, or runtime wiring.

The V1 execution boundary is deliberately narrow:

- only the six existing low-risk declarative families implemented by
  `autonomy_growth.solver_executor.execute_artifact` are accepted; arbitrary
  Python, generated source, callbacks, subprocesses, tools, and LLM calls are
  outside this runner;
- artifacts, configs, inputs, expected values, and declared-unit payloads must
  be byte-canonical strict JSON. Per-object byte, JSON depth, node,
  collection-size, total-corpus, repeated artifact-decode work, projected
  output-byte-work, and 20--4096 case bounds are checked before the first arm
  executes. A family-specific preflight
  also validates the exact numeric/list/object/operator/method shapes consumed
  by each interpreter, preventing a bounded string from masquerading as a very
  large coefficient or column sequence. The returned output-size limit is
  necessarily checked immediately after each arm returns;
- candidate and incumbent must have the same supported family kind. The
  runner domain-hashes the detached artifacts and configs and checks those
  digests, the family digest, materialized case/unit/expected commitments,
  selection manifest, pair manifest, holdout-pack commitment, exact arm-order
  assignment, holdout-access contract, oracle contract, and resource-policy
  digest against the plan before execution;
- HMAC case leaves bind the campaign, declared-unit commitment, and actual
  canonical input. The pack also binds precommitted expected-value leaves and
  the deterministic assignment. This detects a wrong key or post-plan payload,
  label, unit, or membership substitution inside this invocation. It does not
  authenticate key custody, prove that the material was temporally sealed, or
  authenticate the receipt's external origin;
- cases are sorted by their pre-outcome case commitments. Candidate-first and
  incumbent-first alternate by sorted rank, yielding 10/10 for 20 cases and
  11/10 for 21. This is deterministic counterbalancing, not random assignment,
  an unbiased experiment, or proof that order effects are absent;
- each arm receives a freshly decoded artifact and input graph from the same
  canonical input bytes. Both arm calls terminate before expected-output
  comparison, and the expected value is never an executor argument. Each output
  is canonicalized and committed immediately. The runner checks post-return
  input and artifact state, but cannot detect mutation that was restored before
  return;
- exact canonical-JSON equality produces PASS/FAIL. When pair integrity remains
  complete, any ordinary exception, including `TimeoutError`, produces ERROR
  and the other arm still runs. If post-return argument state differs, both
  outcomes become NOT_SCORED instead. C7 has no interrupting process boundary,
  so it never emits or claims an enforced timeout;
- a post-return argument mismatch makes the pair incomplete. C6 then remains
  inconclusive rather than treating the row as lift evidence.

The public C7 receipt contains only aggregate counts and digests. It exports no
case/unit/pair leaves, raw inputs, expected values, solver outputs, HMAC key,
exception details, paths, timestamps, random values, or UUIDs. Its constructor
rechecks count, policy, C6-root, literal-fact, and authority relationships.
Receipt digests provide deterministic integrity only; receipt-origin
authentication remains false.

This runner can truthfully report that two fresh argument graphs came from the
same canonical bytes at its API boundary, that the precommitted expected output
was omitted from the closed executor arguments, and that its commitments
matched the supplied plan.
It cannot prove solver determinism outside this invocation, actual held-outness,
candidate-development leakage isolation, oracle organizational independence,
statistical-unit independence, effective sample size, cross-campaign
multiplicity control, significance, or a causal effect. C6 therefore continues
to serialize all of its independent-verification fields as false even when C7
created the observations. C7 also does not externally pin or independently
verify the supplied plan, registry snapshot, runner source identity,
toolchain/environment, or the semantic relationship between the committed
cell/subdivision addresses and the artifacts. Replacing those assertions makes
a different self-consistent plan; it does not create externally authenticated
evidence.

The request has no execution-callback field and C7 selects only the closed
executor API. Python module bindings and the runner artifact are not externally
authenticated, however, so the receipt does not claim that executor effects
were independently verified. Its top-level authority fields say that routing,
promotion, BuilderHost, registry writes, external writes, and runtime authority
were not *requested by this runner*. The nested C6
`external_writes_applied: false` fact remains scoped to C6's pure aggregate
accountant, not an independent attestation of the surrounding Python process.

C7 is not the coding sandbox described above. It is a bounded in-process
interpreter for reviewed templates, which is the efficient path for large
numbers of solver instances: one reviewed family implementation can serve many
hex-addressed configurations. A future free-form family builder still needs a
fresh process or container, immutable mounts, no network by default, hard CPU,
memory, process and wall-clock enforcement, secret isolation, signed artifacts,
and a verifier outside the candidate process. C7 grants none of that authority.

### C8a inert coding-candidate preflight

C8a adds
`waggledance/core/learning/understanding_coding_candidate_builder.py` and the
private standalone worker
`waggledance/core/learning/_understanding_coding_candidate_worker.py`. The API
is default-OFF. In `STATIC_SHADOW` it accepts one post-generation source pack.
One call accepts one source pack. C8a cannot prove that a new family primitive
is needed, deduplicate across campaigns, prevent one call per solver instance,
or demonstrate 50,000-solver scale. Those remain explicit false receipt facts.
The intended later integration places an external gap-and-deduplication gate in
front of this expensive path. The scalable target remains reviewed family
implementation × declarative configuration × many registered descriptors;
C8a creates no descriptors and does not establish that BuilderHost can avoid
hand-writing 50,000 source trees.

The private request contains exact source bytes plus digest-bound provenance
and a local cell binding. It has no caller-supplied command, executable,
working directory, path, environment, mount, callback, provider, expected
output, commitment key, or executor seam. Before launch the controller:

- snapshots and revalidates every raw plan, policy, source-pack, and cell-binding
  field, then rechecks the source manifest and policy digest;
- recomputes the supplied `CellIdentityV1` and `GenesisLineageV1` entry and
  requires both to name the same content-addressed cell;
- recomputes a binding over that identity, the logical fenced
  `HexCellAddressV1`, subdivision address, and supplied registry-snapshot
  digest;
- rehashes the one private worker and the on-disk CPython executable selected by
  the current interpreter path before launch, then matches both to the plan.

These are same-invocation byte and relation checks. A lineage entry does not
prove registry closure or ancestry, a supplied registry digest is not an
external pin, and associating a logical hex address with a content-addressed
identity does not independently attest the relationship. Different hashes do
not establish distinct Genesis origins, organizations, methods, evidence
sources, operators, or physical failure domains.

The launcher selects an absolute interpreter and worker itself, applies fixed
`-I -S -E -B` flags, creates and removes a local disposable current directory,
passes source to the worker only through size-bounded canonical JSON on stdin,
supplies a minimal environment, disables the shell, incrementally caps stdout
and stderr, and enforces and reaps a direct-child wall timeout on every
post-launch path. The controller-selected local-TCB worker rechecks canonical
protocol shape, absolute policy ceilings, the policy digest, fixed
interface/AST/packaging digests, source bounds, and the source-manifest digest.
It bounds lines, tokens, AST nodes, AST depth, literals, and integer digits,
requires the fixed `solve(payload)` and `test_*()` interfaces, then uses
`ast.parse` and `compile(..., dont_inherit=True, optimize=0)`. It carries but
does not independently authenticate the plan, cell, worker, or interpreter
digests. It never imports the candidate, evaluates its statements, executes its
tests, or emits source or compiler details.

After a successful static compatibility screen the controller separately
reconstructs a canonical in-memory package containing only `solver.py`,
`test_solver.py`, and their manifest. It contains source, not bytecode, and is
not persisted or registered. Worker-reported source, manifest, and package
digests must match the controller's reconstruction. A rejection, crash,
timeout, output flood, malformed protocol, or digest mismatch returns no
artifact. The aggregate receipt contains bounded status, counts, and digests;
it omits source, stdout, stderr, exceptions, paths, environment, PID,
timestamps, hostnames, and randomness.

The worker process contains parser/compiler crashes and stalls outside the
controller process. It does **not** confine generated-code execution: the
candidate never executes. C8a therefore hard-codes false for OS sandbox,
generated-code process isolation, filesystem/network/environment/secret
isolation, CPU/memory/disk quotas, process-tree termination, code safety,
behavioral correctness, lift, independent verification, independently verified
Genesis origin, echo-chamber absence, Genesis external pinning, family
novelty/deduplication, cross-campaign single-attempt enforcement, mass
custom-code generation, C7 execution, BuilderHost/provider invocation,
MAGMA/hive writes, routing, registration, promotion, runtime, and
product/external-system write authority. This last false fact does not deny the
explicit local temporary-directory creation and removal described above.
The application Dockerfile, a worktree, the older `TemplateCompiler` AST
screen, and `ClaudeCodeBuilder` are not accepted as sandbox evidence.

C8a does not submit artifacts to C7. Current C7 accepts exactly six reviewed
declarative families and rejects the C8a package format because it has no
supported declarative `kind`. A later isolated runner must use a
pinned dedicated OCI image, no network, immutable/read-only mounts, a numeric
non-root user, dropped capabilities, no-new-privileges/seccomp, secret
separation, hard CPU/RSS/PID/disk/output limits, full container-tree cleanup,
and an outside verifier. The current 2026-08-03 host probe found no Docker,
Podman, or `bwrap`, so absence must fail closed; native Python is not a fallback
sandbox.

## Two-week sprint ledger

| Slice | Deliverable | Status |
|---|---|---|
| C1 | frozen contracts, privacy, lineage, hard-false authority | pushed |
| C2 | bounded two-phase loop, secret nonce, atomic runtime seam | pushed |
| C3 | durable ledger, replay/restart, authenticated WDP/recovery | pushed |
| C4 | default-OFF wiring, semantic-domain guard, docs, executable harness | pushed at `3f443301` |
| C5 | accounting closure, plaintext-retention truth, WAL process-crash drill | pushed at `61fc99b`; CI text repair at `07c498e` |
| C6 | default-OFF, raw-free paired solver-lift evidence contract | pushed at `48694a9`; local/full CI and Tools exact-head review green |
| C7 | closed declarative paired runner feeding C6 | pushed at `fc4474b`; local full suite and exact-head CI green |
| C8a | inert static coding-candidate compile/package preflight | implementation checkpoint; default-OFF, candidate never executes, no sandbox/runtime authority |
| Gate | exact pushed-head reviews and CI | C7 green; C8a pending |

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
raw-free projection, semantic-domain binding, explicit plaintext-retention
truth, authenticated STOP, shared-key quorum refusal, revision bounds,
stale-fence recovery refusal, fenced rebuild planning, direct object state,
raw ledger events, and literal-false authority flags. Its JSON is aggregate
evidence, not a claim-safe or activation artifact.
The `external_writes_applied: false` gate means no product or external-system
write was performed; the harness necessarily writes its disposable local
SQLite database and, when requested, the explicit local `--out` report.

## Exit criteria before any wider activation

Shadow activation remains a separate operator decision. Before even a narrow
synthetic live adapter is considered, require:

- exact pushed head and green CI;
- independent harness and dual RCO review;
- explicit plaintext-retention truth contract (present), plus an approved
  bounded/encrypted V2 retention and checkpoint policy before wider use;
- ledger rotation/archive design without breaking replay;
- metrics for audit gaps, pending expiry, quarantine, budget drops,
  calibration, and STOP/challenge outcomes;
- process-crash/reopen drill (present), plus deployment-filesystem and
  power-loss/corruption drills;
- no private/restricted raw-value path;
- proof that every authority flag remains false.

Before routing, action, builder, promotion, Dream, live recovery, or hive
commit authority, require a new scoped design and explicit authorization. None
of those powers is implied by this V1 implementation.
