# D1 PII scrub boundary — preparation only

> **D1-PREP redacts only two settings paths. The other 203 matched
> current-tree paths remain unresolved, unclassified, and unchanged.**

This document defines safety boundaries for D1. It is deliberately
non-operational: it contains no procedure for changing Git history or remote
state and grants no authority to do so.

The current-tree observation is bound to pre-prep `origin/main`
`f12f6d971accf5717141b7bfa2f54a7a35628f91`:

- 205 paths in the matched-path union;
- 15 paths containing the business-ID-shaped value;
- 3 legal KEEP paths;
- 2 settings paths redacted by D1-PREP;
- 203 paths remaining for operator/legal classification.

The legal KEEP paths are exactly `LICENSE`, `LICENSE-BUSL.txt`, and
`NOTICE`. Their contents are not authorized for alteration by D1-PREP.

Status is `prepared_blocked`. `blocked_scope` remains true. No PREP
observation, successful test, local file, digest, or self-attestation grants
scope, legal, release, production, or execution authority.

## D1-PREP — NON-DESTRUCTIVE

D1-PREP is limited to normal, reviewable source changes:

1. Replace the three direct `facts.business_name`, `facts.owner`, and
   `facts.y_tunnus` scalars in `configs/settings.yaml` and
   `backup/2026-04-23/settings.yaml.pre-hybrid` with their fixed
   field-specific placeholders.
2. Add the current-tree-only, non-authoritative lineage template.
3. Reduce the D1 tool to bounded read-only inventory and inspection behavior.
4. Add fail-closed tests for parser, capture, repository-shape, scanner,
   lineage, and Phase-10 invariants.

The two settings edits are a HEAD configuration redaction only. They do not
show that the 203 other matched paths are safe, synthetic, legally mutable, or
absent from any history.

All PREP reports must retain `blocked_scope: true` and false authority flags.
They may describe only whether a bounded observation completed. They must not
label a repository, mirror, remote, release, scope, or history as clean,
complete, approved, or executable.

External sensitive-value inventory is operator-local and untracked. Its
capture must be bounded and identity-stable, reject ambiguous JSON and unsafe
filesystem objects, and serialize neither raw values nor value-derived
fingerprints. A supplied ref/OID manifest can establish local byte-for-byte
consistency only; without authenticated remote binding it says nothing about
production state.

Repository inspection is fail-closed. Shallow or non-bare sources,
alternates, replacement refs, grafts, partial/promisor state, integrity
failures, ref drift, stored-versus-reachable object disagreement, unsupported
objects, undecodable metadata, or interrupted observation make the result
unverifiable. Inspection must observe underlying objects without replacement
substitution.

Byte scanning classifies each exact Git path occurrence independently,
including paths present only in older trees. Shared blob identity never
transfers a decision from one path to another. Legal KEEP hits, every path
outside the two settings paths, and every metadata hit remain blockers.

PREP completion means only:

- the seven-path source change is reviewed;
- strict hermetic D1 tests pass without an execution dependency;
- the selector-required full suite and exact-head CI pass;
- focused security, Tools-conformance, and independent review gates pass;
- the merged state is still `prepared_blocked` and the release remains on
  hold.

## OPERATOR/LEGAL SCOPE DECISION REQUIRED

Before any later D1-EXEC design, the operator and legal owner must classify
all 205 observed paths and all later-discovered historical or metadata
contexts. At minimum, the decision must resolve:

- whether each of the 15 business-ID paths contains real, synthetic, or
  intentionally public material;
- whether personal and business attribution in licenses, notices, package
  metadata, and source headers must remain unchanged;
- every exact path that is KEEP or REDACT, including deleted historical paths;
- every shared blob that occurs under paths with different classifications;
- every author, committer, tagger, commit-message, and tag-message match;
- the complete remote ref population and any signed-tag or release impact;
- custody and retention rules for contaminated material;
- the authenticated evidence and trusted verifier for the decision.

The classification must be signed through a separately approved trust path
and bound to exact path bytes, expected preimages, repository identity, and
the relevant ref snapshot. D1-PREP has no signature verifier and cannot accept
a committed file, locally supplied manifest, status edit, or boolean claim as
authority.

If any path, metadata record, object, ref, encoding, signature, or legal
classification is missing or ambiguous, scope remains blocked.

## D1-EXEC — OPERATOR-ONLY CONSTRAINTS

D1-EXEC does not exist in this slice. This section is a constraint boundary,
not an operational procedure.

Any future D1-EXEC proposal must be implemented and reviewed separately. It
must:

- use a path-aware executor bound to the authenticated classification;
- authenticate the operator/legal classification before doing any work;
- bind the source to an authenticated remote identity and exact ref/OID
  snapshot;
- reject shallow, alternate, replacement, grafted, partial, promisor, corrupt,
  drifting, or object-incomplete repositories;
- act by exact path and expected preimage rather than by global content
  substitution;
- preserve every KEEP context byte-for-byte;
- treat a shared blob separately for each classified path and materialize a
  distinct result where path decisions differ;
- cover deleted historical paths, all approved refs, and supported metadata
  contexts;
- fail on unclassified, unexpected, undecodable, missing, extra, or changed
  input;
- prove that the resulting current tree matches the separately approved HEAD
  state and that no unintended path changed;
- produce independently authenticated evidence suitable for a separately
  reviewed Phase-10 exception;
- require explicit operator authorization at the point of irreversible remote
  change.

No PREP artifact authorizes D1-EXEC. In particular, changing the lineage
status to `executed` would be a self-attestation and must fail the PREP
regressions.

## Lineage boundary

`docs/security/d1_pii_scrub_lineage.json` records only the five
current-tree aggregates above, bound to the pre-prep SHA. Its history, refs,
mirror, and execution fields are null. It contains no raw sensitive value,
value-derived hash, path inventory, shallow ref count, remote claim, signature,
or execution evidence.

The Phase-10 ancestry invariant remains unconditional during D1-PREP. Missing,
prepared, locally edited, or committed self-declared lineage cannot excuse a
non-ancestor result. A future exception requires a distinct, independently
authenticated evidence design reviewed with D1-EXEC.

## Rollout and rollback boundary

D1-PREP lands through the normal reviewed source workflow and remains a
release hold. Operator/legal classification and D1-EXEC are later, separate
decisions.

Before merge, a failed PREP review means the branch is abandoned. After merge,
corrections use another normal reviewed source change. Raw settings values are
never restored; a functional correction must retain safe placeholders or use
another approved non-sensitive configuration.

Because D1-PREP changes no history or remote state, it has no destructive
rollback. After any future externally authorized history change, remediation
must move forward from sanitized state; contaminated public history must never
be restored.
