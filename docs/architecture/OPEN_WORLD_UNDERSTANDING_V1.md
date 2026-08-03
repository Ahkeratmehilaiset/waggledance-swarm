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

### C8b supplied-snapshot capability accountant

C8b is a default-OFF, pure accountant over one caller-supplied snapshot of
declared capabilities. In its enabled evidence-only mode it applies bounded,
exact equality and returns exactly one disposition: `REFUSED`,
`EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_SNAPSHOT`, or
`NO_EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_SNAPSHOT`. An exact match means
only that the compared declarations are equal inside that supplied snapshot.
It does not prove reuse, novelty, semantic equivalence, or deduplication, and
there is not yet any prior-attempt or cross-campaign deduplication state.
Its matching-policy digest commits to the closed family vocabulary,
canonical/sorted snapshot rules, duplicate refusal, and ambiguous-match
refusal. The receipt reconstructs its bounded policy, raw-free campaign-digest
plan, and request relations, then checks count-derived canonical byte, node,
and depth feasibility before accepting its outer content digest.
That public digest is not origin authentication: snapshot counts, a matched
entry digest, and caller-supplied gap evidence remain unauthenticated
observations, stated explicitly by `receipt_origin_authenticated=false`.

C8b has no C8a, C7, BuilderHost, provider, storage, registry, MAGMA
ledger/storage, routing, promotion, runtime, execution, or sandbox wiring. It
uses only the existing pure canonical hashing helper, neither imports nor
executes candidate code, and grants no write or activation authority. All
Genesis, hex, echo-chamber, and 50,000-solver claims remain false. A fresh
provider-default Grok advisory returned `NARROW`. Three separate read-only
in-process API, security, and claim reviews later found no implementation
blocker. A separate Tools review of implementation SHA `32f85305` likewise
found no implementation or adversarial-test-path blocker and requested the
evidence wording and ledger correction recorded here. These reviews are
advisory only, not votes, gates, or authority grants.

### C8c supplied expected-digest relation accountant

C8c is a default-OFF, pure equality accountant over one raw-free C8b receipt
and one separately supplied expected-digest object. The expected object is a
keyword-only input rather than a field inside the request. In `STATIC_SHADOW`
mode C8c revalidates and defensively copies the complete C8b receipt and the
expected object, then compares family-snapshot digest to expected
family-snapshot digest and registry-snapshot digest to expected
registry-snapshot digest. It has no plan or attempt budget because it schedules
nothing and grants no action.

The public result is only `EXPECTED_DIGEST_RELATION_HOLDS`,
`EXPECTED_DIGEST_RELATION_MISMATCH`, or `REFUSED`. A presented C8b
`REFUSED` disposition dominates and produces `SOURCE_C8B_RECEIPT_REFUSED`
with both equality fields absent. Every non-refused result preserves the
complete source C8b receipt, so its exact-match or no-match disposition remains
separate from the C8c equality relation. A C8b no-match plus a C8c equality
result is still neither novelty nor permission to generate.

The word `pin` in the module and schema names denotes only the supplied
expected-digest bundle and its domain-separated content digest. It does **not**
mean that an external trust anchor exists. A caller can construct a
self-consistent C8b receipt and matching expectations without providing raw
snapshot bytes; C8c can truthfully report local equality in that case. The
receipt therefore fixes `snapshot_externally_pinned`, pin provenance and origin
authentication, C8b receipt-origin authentication, catalog authenticity,
freshness, completeness, and independent registry identity to false. It also
fixes snapshot-byte rehashing, signatures, HMAC, semantic equivalence,
deduplication, novelty, reuse/build eligibility, prior-attempt enforcement,
generation, execution, sandbox, Genesis/hex/echo-chamber, 50,000-scale, writes,
routing, promotion, and runtime authority to false. A caller-supplied HMAC key
was deliberately omitted because it would add secret handling while proving
only possession of another caller-supplied value.

The C8c receipt nests both raw-free inputs and reconstructs the relation-policy,
policy, expected-object, source C8b receipt, request, equality/disposition, hard
claim, and outer-receipt digest relations. It invokes C8b receipt validation but
does not invoke the C8b snapshot evaluator, read raw snapshot bytes, or perform
filesystem, network, subprocess, registry, MAGMA, or external-system I/O.
Default OFF creates a fresh policy per call and returns before inspecting the
request or expected object.

A fresh provider-default Grok advisory ranked this detached expected-digest
seam ahead of attempt history, a knowledge-delta extension, and any C8b-to-C8a
adapter. Read-only contract, adversarial, API, and security reviews converged on
the same narrow claim boundary; the first API review found and caused fixes for
ambiguous C8b invocation wording and a mutable default-policy object. Their
current-byte rechecks found no remaining local blocker. These are advisory
observations only. A genuine external pin still requires an independently
configured trust root, signature and signer authorization, freshness,
revocation, and anti-replay. The next safe accounting slice is supplied
attempt-history equality; no C8a/C7 or generated-code execution wiring follows
from C8c. A separate Tools exact-head review was requested twice for
implementation SHA `1cd645c0`; both bridge consumer ticks timed out without a
review result. Those timeouts are neither approval nor rejection and are not
counted as review evidence.

### C8d supplied declared-attempt snapshot accountant

C8d is a standalone, default-OFF, pure accountant over one bounded canonical
caller-supplied declared-attempt snapshot. Its only matching subject is the
exact `declared_capability_fingerprint`; campaign, cell-binding, evidence, and
record digests are audit metadata rather than additional identity predicates.
The request does not carry an expected value. Instead, a separate keyword-only
caller-supplied expected object supplies the snapshot digest to compare. C8d
does not consume or invoke C8b, C8c, C8a, or C7, and it does not infer a
generation intent, attempt outcome, state transition, or chronology.

In the enabled static-shadow mode, the accountant defensively copies the raw
snapshot bytes and expected object, then validates every supplied field under
finite entry, canonical-byte, nesting-depth, and node bounds. Canonical
ordering and refusal of structurally duplicate record identifiers or evidence
digests make the supplied object unambiguous to parse; they are
input-validation rules, not proof of global attempt deduplication. The
expected-digest comparison happens before any subject scan. A mismatch
dominates, returns `REFUSED`, and exposes neither a subject-match count nor a
selected record digest.

When the supplied expected digest holds, zero exact subject matches reports
only that no equal fingerprint occurs in this snapshot. One exact match reports
only that one equal fingerprint occurs in this snapshot and may identify that
canonical record. More than one exact subject match is ambiguous and returns
`REFUSED`; C8d never selects, merges, ranks, or treats the entries as a retry
history. In particular, zero matches is not novelty, build eligibility,
generation permission, retry permission, or evidence that no attempt exists
elsewhere.

Both the snapshot and its expected digest remain caller supplied. A caller can
self-mint a consistent pair, replay a stale pair, omit records, or present a
fork. Because the public receipt is raw-free, its validator cannot rederive
the reported count or selected-record digest from omitted rows; a caller can
also remint those internally consistent receipt fields. They remain
non-authoritative because receipt origin and every action-authority claim are
hard-false. Consequently, C8d proves no origin authentication, external pin,
completeness, freshness, anti-replay, anti-rollback, chronology, attempt
occurrence, outcome, semantic capability equivalence, deduplication, durable
reservation, or cross-campaign/cross-cell single-attempt enforcement. It has no
sandbox, generated-code, BuilderHost/provider, storage, registry, MAGMA,
routing, promotion, runtime, execution, external-system write, or activation
authority.

The truthful hex relationship at C8d is limited to a repeatable pure contract
whose exact capability key could later support a shard-local index. C8d does
not itself distribute work, bind a durable cell, recover a dead cell, send ring
messages, subdivide a cell, perform atomic compare-and-swap, demonstrate
50,000-solver scale, or prevent echo chambers. Those properties require a
later authenticated durable attempt journal, a Merkle-backed or equivalently
verifiable sharded index, and an atomic per-capability compare-and-reserve
operation. Cell recovery must resume the same durable reservation rather than
mint a new local attempt.

A fresh provider-default Grok analysis and the local contract, bounds,
semantic, and adversarial reviews are design advice only. They are not votes,
runtime gates, trust anchors, or authority grants. The implementation was
pushed at `a4ef2bbe`; 80 focused and 329 compatibility tests passed locally.
The selector-requested local full suite timed out after 30 minutes without a
result and is not counted as pass or failure evidence. Local API and claim
rechecks found no blocker. A separate Tools exact-head read-only review passed
with 80 focused and its 267-test compatibility selection. Exact implementation
HEAD Tests `30836217801` and WaggleDance CI `30836219973` completed
successfully. None of these results grants runtime or action authority.

A post-implementation provider-default Grok review accepted C8d only as this
narrow pure accountant. It selected a standalone default-OFF supplied
reservation candidate-transition accountant as the next safe prerequisite:
such a slice may report only whether a local compare-and-reserve precondition
would hold in supplied state. A durable atomic reservation store, BuilderHost
wiring, and dead-cell owner handoff remain separate later authority and storage
boundaries.

### C8e supplied reservation-state CAS-precondition relation accountant

C8e is a standalone, default-OFF, pure relation accountant over one bounded
canonical caller-supplied reservation-state snapshot. The input is shaped like
current state solely so an exact capability key can be checked against one
finite object. C8e does not verify that the object is current, durable,
complete, authentic, globally unique, or related to any earlier object. Its
only exact root fields are `schema_version`, `reservation_scope_digest`, and
`reservations`. Each reservation row has exactly seven fields:
`reservation_id`, `declared_capability_fingerprint`, `state`,
`cell_binding_digest`, `campaign_id_digest`, `intent_digest`, and
`state_evidence_digest`. There is deliberately no `snapshot_id`, event journal,
sequence, predecessor, timestamp, lease, revision, or chronology claim.

The canonical row order is capability fingerprint, reservation identifier,
campaign, cell binding, intent, state, and evidence digest. Reservation
identifiers and exact capability fingerprints are each unique within the
supplied object. Evidence-digest duplicates are intentionally allowed because
evidence is a non-key audit commitment, not reservation identity. The bounded
parser accepts at most 2,048 rows and 2 MiB of canonical UTF-8 JSON, with
maximum nesting depth 6 and maximum node count 32,768. These are parser and
resource bounds only; they do not establish a 2,048- or 50,000-solver operating
capacity.

The stable reservation identifier is derived only from the caller-supplied
`reservation_scope_digest` and exact `declared_capability_fingerprint` under a
fixed domain separator. Campaign, cell, intent, state, and evidence are not
identifier inputs, so changing any of them cannot mint another reservation key
for the same exact capability inside the same supplied scope. This deliberately
excludes campaign even though the provider-default Grok design sketch proposed
a campaign-inclusive identifier: campaign inclusion would permit
across-campaign key evasion inside one scope. The scope remains unauthenticated
and caller selectable, so this choice proves no global or cross-scope
enforcement. Changing a cell binding is audit-visible metadata, not a handoff,
owner-fence transfer, or dead-cell recovery operation.

The expected object and transition proposal are separate keyword-only inputs,
and both require exact types and exact field sets. Evaluation is two-phase.
The request, expected object, and proposal first pass syntactic validation,
while the snapshot passes bounded canonical structural validation. An expected
snapshot-digest mismatch then dominates: it returns `REFUSED` before any row
identity or proposal identity is derived, before a subject lookup, and before
binding or state checks. Only when that caller-supplied equality holds are all
row identities and the proposal identity derived. A row or proposal whose
claimed reservation identifier does not equal the stable derived identifier is
malformed contract input and raises `AttemptReservationCasContractError`; it is
not a negative transition result. A derived identifier that resolves to a row
for another capability is `REFUSED`. `COMMIT_IF_RESERVED` and
`ABORT_IF_RESERVED` additionally require the proposal's exact capability,
cell, campaign, and intent bindings to match the reserved row before its state
can support a candidate precondition.

The complete supplied-state relation is:

| Proposal | Supplied row | Exact required bindings | Local result |
|---|---|---|---|
| `OPEN_IF_ABSENT` | absent | derived identifier holds | candidate open precondition holds |
| `OPEN_IF_ABSENT` | `RESERVED` | capability key already present | candidate open precondition does not hold |
| `OPEN_IF_ABSENT` | `COMMITTED` or `ABORTED` | capability key already present | candidate open precondition does not hold |
| `COMMIT_IF_RESERVED` | absent | n/a | candidate commit precondition does not hold |
| `COMMIT_IF_RESERVED` | `RESERVED` | capability, cell, campaign, and intent all equal | candidate commit precondition holds |
| `ABORT_IF_RESERVED` | absent | n/a | candidate abort precondition does not hold |
| `ABORT_IF_RESERVED` | `RESERVED` | capability, cell, campaign, and intent all equal | candidate abort precondition holds |
| `COMMIT_IF_RESERVED` or `ABORT_IF_RESERVED` | any bound row | a required binding differs | refused before state acceptance |
| `COMMIT_IF_RESERVED` or `ABORT_IF_RESERVED` | `COMMITTED` or `ABORTED` after binding checks | all required bindings equal | terminal conflict |

`COMMITTED` and `ABORTED` are terminal in this local relation. An open proposal
cannot reopen either state, and commit or abort cannot transform one terminal
state into another. This is not lease expiry, retry policy, or proof that a
durable implementation has burned the key.

A positive C8e result means only that a candidate transition precondition holds
in the exact caller-supplied bytes evaluated by that call. It is not a
reservation and it is not a successful compare-and-swap. In particular, two
parallel evaluators can both report that the local open precondition holds
against the same supplied empty snapshot. Both reports can coexist; neither
excludes the other, orders the other, or reserves anything. C8e has no lock,
linearization point, revalidation under a store transaction, atomic write, or
successor snapshot. Applying a transition remains outside this contract.

The public receipt is raw-free and therefore intentionally remintable within
its structural bounds. For a declared zero-row snapshot, its validator
rederives the unique empty-snapshot digest from the schema and supplied scope
and forbids any matched-row or observed-state claim. For a non-empty declared
count, however, the receipt omits the supplied rows, so its validator cannot
independently rederive the reported lookup, binding, or candidate-precondition
result. A caller can still reseal a structurally plausible non-empty semantic
outcome. Internal receipt digest consistency proves neither origin nor truth of
the omitted input. Every
durability, CAS, store, apply, currentness, freshness, ABA protection, history,
chronology, origin authentication, authorization, handoff, build, generation,
runtime, MAGMA, registry, sandbox, routing, promotion, 50,000-scale, and
echo-chamber claim remains literal false. C8e neither consumes nor grants any
such authority.

A fresh provider-default Grok analysis recommended building this bounded pure
precondition relation while holding the durable store and BuilderHost path.
Local contract, boundary, and adversarial red-team reviews independently
converged on the supplied reservation-state shape, stable capability key,
explicit TOCTOU counterexample, terminal no-reopen rule, and hard-false
authority boundary.
Those reviews are advisory rather than votes, currentness proofs, or activation
gates. C8e locks only this narrow contract. A durable reservation store and
atomic apply path, authenticated scope, ABA-safe revision/fencing, BuilderHost
wiring, dead-cell handoff, and any generated-code execution remain `HOLD`
without separate implementation scope and authority.

A post-implementation provider-default Grok read-only review at exact pushed
HEAD `02ed94e6749da215c8fea1e0b5b229502f36f4ed` found no C8e contract blocker.
It recommended one additional pure prerequisite before any durable writer:
a standalone default-OFF successor-snapshot transition relation accountant.
That possible C8f would deterministically account for the canonical successor
snapshot implied by one locally holding C8e precondition while still proving
no write, exclusion, currentness, or atomicity. C8f was not implemented or
authorized by C8e; it was subsequently implemented under a separate bounded
claim. Durable apply, authenticated scope, revision/fencing,
BuilderHost, sandbox execution, and recovery handoff remain later `HOLD`
boundaries. The Grok result is advisory only and grants no review, merge,
release, runtime, or activation authority.

The local implementation gate passed 93 focused tests and a 422-test C8a-C8e
and C7 compatibility selection. Python compilation, pyflakes, and diff checks
also passed. The fail-safe selector requested the full suite because this
architecture document changed. That local full-suite attempt reached 29%
without a reported failure before the explicit disk guard stopped its owned
process tree below the 100 MiB free-space threshold; exit `-1` is no test
result and is counted as neither pass nor failure. Tools independently returned
`PASS` for exact pushed HEAD `02ed94e6749da215c8fea1e0b5b229502f36f4ed`
after diff, in-memory compilation, and eight in-memory adversarial assertions;
it made no source edit and granted no authority. Exact-head GitHub Tests
`30843741995` and WaggleDance CI `30843744657` completed successfully.
RCO/Fable retrospective checks remain deferred by operator decision until
their weekly usage limits reset.

### C8f pure successor-snapshot transition relation accountant

C8f adds
`waggledance/core/learning/understanding_attempt_reservation_successor.py`.
It is default-OFF and composes the public C8e evaluator exactly once. One
`STATIC_SHADOW` call receives one exact immutable caller-supplied reservation
snapshot, the existing separate keyword-only expected digest, and the existing
separate keyword-only transition proposal. C8f passes the same snapshot bytes
to C8e. It attempts successor derivation only when C8e reports that the exact
precondition relation holds. A C8e refusal leaves the C8f successor relation
unevaluated; a non-holding C8e precondition produces no successor.

The pure transition table is deliberately small:

| Holding C8e precondition | Locally derived successor relation |
|---|---|
| `OPEN_IF_ABSENT` | insert one canonically sorted `reserved` row |
| `COMMIT_IF_RESERVED` | replace the exact matching row state with `committed` |
| `ABORT_IF_RESERVED` | replace the exact matching row state with `aborted` |

Each positive transition derives a new `state_evidence_digest` from a fixed
domain, the base snapshot digest, proposal digest, reservation id, transition,
and target state. Incidental policy bounds are intentionally excluded, so the
same base snapshot and proposal produce the same successor commitments under
different admissible bounds. The successor is canonicalized and then
revalidated through C8e's public snapshot-digest API. C8f does not import a C8e
private parser. Opening at the configured record limit or producing bytes over
the configured byte limit returns a bounded no-successor outcome even if the
source precondition itself held.

The successor byte count is also an exact structural relation rather than a
free receipt claim. For open, it is the validated base byte count plus the
canonical inserted-row byte count and one array separator when the base is
non-empty. For commit or abort, it is the base byte count plus the exact target
state-token length delta; the replaced evidence digests have fixed equal
length. A byte-limit refusal is valid only when this derived count exceeds the
configured limit and a full open has not already taken deterministic
record-limit precedence.

Successor JSON depth and node count are exact from the successor record count:
an empty snapshot has depth two and four nodes, while every non-empty
reservation snapshot has depth four and `4 + 8 * record_count` nodes. C8f uses
the deterministic resource precedence `record`, then `byte`, then `depth`, then
`node`. A source snapshot that fits a restrictive depth or node policy but
whose open successor would not fit now returns an explicit bounded
no-successor relation instead of leaking a predictable contract error.

The public C8f receipt is raw-free. It carries the validated raw-free C8e
receipt plus the successor snapshot digest, ordered record digests, record and
byte counts, target state, and derived evidence digest only on a positive
relation. It never returns successor bytes. The proposal, target state, and
derived evidence reconstruct the transitioned row, so its exact record digest
must occur exactly once and the successor byte count is fixed. Digest
commitments for other untouched omitted rows remain intentionally publicly
remintable: without those base rows, a receipt consumer cannot independently
prove their content. Receipt origin authentication therefore remains literal
false. This is an audit/accounting relation, not an authenticated
state-transfer artifact.

Two parallel C8f calls over the same empty supplied snapshot and open proposal
can both return the same successor digest. That agreement is determinism, not
exclusion. No store head is consulted; no lock, lease, revision, fence,
linearization point, revalidation under a transaction, compare-and-swap,
write, append, or persistence occurs. The TOCTOU window remains open. Every
durability, currentness, authenticity, ABA, concurrency-safety, cross-cell or
global single-attempt, BuilderHost, generated-code, sandbox, MAGMA, registry,
routing, promotion, recovery, handoff, runtime, 50,000-scale, and echo-chamber
claim remains literal false. C8f invokes C8e but does not invoke C8a-C8d or C7.

The truthful hex contribution is one repeatable deterministic state-transition
relation that is contract-compatible with a possible later shard-local writer;
this is neither roadmap approval nor inherited write authority. C8f does not
make a cell a writer, replicate its state, rebuild a dead cell, or authorize
another cell to take ownership. Reservation identity still uses only the
supplied scope digest plus exact capability fingerprint. Independence axes
such as Genesis, method group, evidence root, provider, and failure domain
remain orthogonal; equal successor digests do not establish epistemic
diversity or remove an echo chamber.

C8f closes only a deterministic pure successor relation over caller-supplied
bytes. Review and CI do not authenticate the scope or state, create a
linearization point, apply a successor, or authorize C8g. No C8g deliverable is
yet implemented or authorized. A future writer must revalidate raw canonical
inputs -- not trust the raw-free and partly remintable C8f receipt -- inside an
authenticated, revision/fence- and ABA-safe transaction with explicit
persistence authority. Durable store/apply, BuilderHost, sandbox execution,
dead-cell handoff, runtime activation, 50,000-scale, and echo-chamber claims
remain `HOLD`.

The local gate passed 57 focused C8f tests and a 150-test C8e+C8f compatibility
run. Compilation, pyflakes, and diff checks also passed. Tests cover all three
transitions, expected-mismatch dominance, terminal and binding failures,
canonical ordering, fixed-domain evidence reconstruction, policy invariance,
configured and 2,048-row resource overflow, parallel identical successors,
nested C8e validation, public remintability, hard-false claims, and forbidden
I/O/runtime seams. The fail-safe affected-test selector requested the full
suite because this architecture document changed. It was not rerun locally:
the available C-drive headroom could not accommodate the previously observed
full-suite footprint while preserving the explicit 100 MiB safety guard. CI is
the authoritative full-suite gate. The first Tools review of
implementation HEAD `372caf15` found that a publicly resealed receipt could
claim an impossible record-limit refusal for commit, abort, or a below-limit
open. The validator now binds that reason only to `OPEN_IF_ABSENT` at a full
base-record limit, with hostile reseal regressions for all three invalid cases.
That finding was a blocker, not approval. A subsequent provider-default Grok
review of fix HEAD `857bab16` found the analogous impossible byte-limit reseal.
The validator now derives the exact successor byte count for every transition,
binds positive receipts to it, rejects impossible byte-limit refusals, and
keeps record-limit precedence for a full open. Empty and non-empty open,
commit, abort, forged positive byte-count, and truthful commit-overflow
regressions cover the boundary. A subsequent local read-only audit found two
more blockers: arbitrary
record commitments could omit the fully reconstructable transitioned row, and
restrictive depth/node policies turned predictable open-successor overflow into
a contract error. Positive receipts now require the exact target-row digest
once, while explicit depth/node resource outcomes and precedence regressions
cover the bounded path. Untouched omitted rows retain only the documented
remintability. A final local read-only adversarial audit and Tools independently
returned `PASS` for exact clean, pushed, upstream-equal implementation HEAD
`1da4cf8a42a3e6500de215608fd916cf59ced34d`. Tools reran all 57 focused C8f
tests, checked the diff and in-memory compilation, and rechecked all four prior
receipt/resource blocker classes without source edits or a local full suite.
Exact-head GitHub Tests `30850479245` and WaggleDance CI `30850479353`
completed successfully, including Python 3.11, 3.12, and 3.13. None of those
results grants authority. Durable apply, authenticated scope,
revision/fencing, BuilderHost, sandbox execution, recovery handoff, and
activation remain `HOLD`.

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
| C8a | inert static coding-candidate compile/package preflight | pushed at `7f6a16dd`; local full suite and exact-head CI green |
| C8b | default-OFF pure supplied-snapshot exact declared-capability accountant | pushed at `32f85305`; 94 focused and 209 compatibility tests green; exact-head Tests `30821832356` and WaggleDance CI `30821835324` green |
| C8c | default-OFF supplied expected-digest relation accountant over one C8b receipt | pushed at `1cd645c0`; 40 focused and 249 compatibility tests green; selector-requested local full suite timed out at 20 minutes without a result and is not counted; exact-head Tests `30828459556` and WaggleDance CI `30828462072` green; Tools review unavailable after two consumer timeouts |
| C8d | standalone default-OFF supplied declared-attempt snapshot accountant | pushed at `a4ef2bbe`; 80 focused and 329 compatibility tests green; selector-requested local full suite timed out after 30 minutes without a result and is not counted; local API/claim reviews and Tools exact-head review found no blocker; exact-head Tests `30836217801` and WaggleDance CI `30836219973` green |
| C8e | standalone default-OFF supplied reservation-state CAS-precondition relation accountant | pushed at `02ed94e6`; 93 focused and 422 compatibility tests passed; selector-requested full suite stopped at 29% by the 100 MiB disk guard with exit `-1`, no result, and is not counted; Tools exact-head review PASS; exact-head Tests `30843741995` and WaggleDance CI `30843744657` green; durable store/apply, BuilderHost, handoff, and activation remain `HOLD` |
| C8f | standalone default-OFF pure successor-snapshot transition relation accountant | pushed at `1da4cf8a`; 57 focused and 150 C8e+C8f compatibility tests passed after record, byte, target-row, and JSON-resource blocker fixes; final local adversarial audit and Tools exact-head review PASS; exact-head Tests `30850479245` and WaggleDance CI `30850479353` green; durable apply, authenticated scope, revision/fencing, BuilderHost, sandbox execution, handoff, and activation remain `HOLD` |
| Gate | exact pushed-head reviews and CI | C8d implementation and ledger-only closure evidence green; C8e local evidence, Tools exact-head PASS, Tests `30843741995`, and WaggleDance CI `30843744657` green; C8f implementation-head local evidence, Tools exact-head PASS, Tests `30850479245`, and WaggleDance CI `30850479353` green; this evidence-only C8f closure head still requires its own exact-head CI before claim release; RCO/Fable retrospective reviews pending by operator decision due usage limits; no activation authority |

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
