# Tracked release evidence commit contract

The soak envelope's `commit` is the declared source subject **S**, not the
commit containing the evidence. Producers run against clean S and retain
their exact source-S checks. Evidence is then committed, producing a later
clean HEAD. Source and evidence commits are different by design.

The boundary's `waggledance.head_soak_binding.v2` permits a tree delta only
when S is an ancestor of HEAD, the canonical envelope changes, and every
other changed path belongs to the following fixed output set. Paths below
are relative to `docs/runs/release_soak_evidence/`:

| Output | Canonical path |
| --- | --- |
| Required envelope | `v3.12.0.json` |
| CI evidence | `v3.12.0_ci_status.json` |
| Docker policy evidence | `v3.12.0_docker_policy.json` |
| Axis A proof | `v3.12.0_axis_a_solver_scale/solver_scale_proof.json` |
| Axis B proof | `v3.12.0_axis_b_hex_aligned_eval.json` |
| Soak log audit | `v3.12.0_soak_log_audit.json` |

This is not a directory-wide allowance. Runtime code, configuration, lock
files, raw soak logs, release notes/readiness, operator decision packs and
all other paths must remain unchanged from S. Required approvals and source
changes must therefore precede selecting S. Independent producers can use
clean worktrees at the same S; their evidence is integrated afterward.

Content-bound security/privacy reports must already be current at S; they
are not permitted post-S deltas. The collector's optional `--history` output
must not be used for this post-S envelope collection: raw history and error
logs are frozen inputs to the soak-log audit. Supporting a post-S history
append would require an additional reviewed, content-validated contract,
not simply another allowed filename.

`carrier_only_delta` retains its literal v1 meaning: it is false when any
sidecar changes. `evidence_only_delta` identifies the eligible v2 tree shape.
Neither field is a content-validation result or release authorization.

All existing content checks remain mandatory: the canonical live release
gate must pass against immutable inputs; Axis artifacts must still bind to
the exact S and its tracked source blobs; canonical inputs and Git state
must remain clean and unchanged across evaluation. An absent, stale,
malformed or incorrectly stamped artifact must still HOLD. Source selection,
signature requirements, timing requirements and final publication authority
are not changed by this storage correction.

The combined real-Git regression verifies source-S Axis attestations and the
later evidence tree together. Its metric fixtures are synthetic: passing
the regression is not evidence of product performance or release readiness.

Implementation status: the tree-binding correction alone is insufficient to
claim release readiness. The immutable live-child dependency/data/Git-record
closure must also support the current Axis verifier before the complete
production path can pass. Existing release and operator blockers remain in
force; this document does not assert that they have been resolved.
