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

The immutable live child includes both Axis attestation helpers and the
fourteen exact inventory paths as data, not extra executable modules. Its
Axis Git adapter answers only source-S commit/blob requests derived from
authenticated objects. It does not enable native Git processes, new paths,
or producer preflight operations. Virtual Windows metadata explicitly
identifies the already authenticated regular files/directories as non-reparse
entries; real-filesystem source checks remain unchanged.

Production-schema regression fixtures execute the unchanged candidate gate
and verifier over a real Git S-to-E chain, including LF/CRLF source bytes,
an executable regular blob, and shared blob content. Both correctly bound
Axis proofs validate; an incorrectly stamped later proof still fails.
Unrelated release blockers continue to produce HOLD in these synthetic
fixtures. Neither a passing test nor an ordinary HOLD report asserts that
the complete release gate is ready.

Docker policy generation still requires the checkout HEAD to equal S.
Evaluation of the stored report instead verifies the declared subject's
tracked source blobs and unchanged worktree content. The stored
`source_git.head` must still equal S, preserving generation provenance;
only that historical generation head is excluded from comparison with the
current evaluation checkout. Every other source binding and operator
authorization check remains mandatory. Existing release and operator
blockers remain in force; this document does not resolve them.
