# EvaluationResult v1 — draft RFC

Status: DRAFT.
Owner: Claude (Phase F PR1 of `valiant-beaming-rocket.md` / 100h plan).
Scope: this document proposes a strictly-additive `magma.evaluation_result.v1`
superset of the committed v0 surface. It does NOT change v0 behavior, does
NOT touch any release-boundary field, and does NOT advance any must-win
axis label.

## Why v1 is needed

The committed `waggledance.core.magma.evaluation_result` shape
(`schemas/v3_13_0/evaluation_result.v0.json`,
`waggledance/core/magma/evaluation_result.py`) binds every MAGMA receipt to
*what happened* (subject_payload digest) and *why it mattered* (verdict +
reason codes + verifier path + risk class). It is the substrate's
canonical evaluation record.

Three pressures push toward a v1 superset:

1. **Confidence provenance is opaque.** v0 carries
   `confidence_score: number` and `uncertainty_sources: list[dict]`, but
   the *basis* of the score (model, sampling, methodology) is not
   recorded. A reviewer cannot tell whether `confidence_score=0.92` came
   from a 10-sample bootstrap, a 1-sample point estimate, or a
   hand-tuned heuristic. Phase D's adversarial-corpus expansion in #599
   surfaced this gap: the new cases need reviewer-visible confidence
   provenance to be RCO-able.
2. **Sanitization audit has no first-class record.** v0 references
   `risk_class` (informational/internal_memory/local_artifact/external_effect)
   but the substrate has a separate sanitization-contract surface
   (PII redaction, locale handling, max-length) that the receipt chain
   does not currently bind. A redacted summary and a leaked summary
   are receipt-indistinguishable today.
3. **Competitor-axis attribution is implicit.** v0 supports
   subject_type=`solver|policy|counterfactual|promotion|peer_review`
   but does not carry which competitor-axis the evaluation contributes
   evidence for (A3 counterfactual, A4 solver-growth, ceded A6/A7/A8).
   The post-#598 baseline.json now records per-axis counts; the
   evaluation record could carry the axis tag for direct join.

## Anti-claim invariants for v1

These are NOT negotiable. The draft includes them up front so the design
choices below are evaluated against the invariants, not against the
convenience of any single use case.

- v1 records are still subject to `release_boundary` operator-gating
  rules. No v1 field upgrades a stable-tag claim, a Docker `:latest`
  move, an `operator_gate_required=false` shortcut for external_effect
  payloads, or a `consensus_grade` flip.
- v1 records do NOT advance any must-win axis claim_label (`A3` and
  `A4`) beyond the qualified set
  (`PUBLIC_DOC_CLAIM`, `MEASURED_LOCAL_PARTIAL`, `MEASURED_LOCAL_SYNTHETIC`,
  `MEASURED_LOCAL`, `MEASURED_NETWORK`). Any unqualified label
  (`PROVEN`, `CONSENSUS_GRADE`, `FULL`, `GA`) stays blocked by
  `tools/magma_slice_counter_read.py` regardless of v1 enrichment.
- v1 is **additive only**. A v0 reader MUST be able to read a v1
  record by ignoring unknown fields; the v0 schema's
  `additionalProperties: false` is the obstacle here (see cross-version
  compatibility below).
- v1 fields are OPTIONAL where v0 has no equivalent; nothing in v0
  becomes required by v1.

## Proposed additive fields

Each entry below identifies the field, its purpose, the v0 gap it
closes, and the structural constraint that prevents overclaim.

### `confidence_basis` (object, optional)

```
"confidence_basis": {
  "method": "bootstrap" | "monte_carlo" | "point_estimate" | "heuristic",
  "sample_count": integer >= 1,
  "model": string,
  "methodology_reference": "path/to/methodology.md or rfc#section"
}
```

Closes pressure 1. The receipt chain now carries the *source* of
`confidence_score`. A reviewer can refuse to RCO a record whose
`method=heuristic + sample_count=1` is being used to claim a
qualified-label upgrade.

### `sanitization_audit` (object, optional)

```
"sanitization_audit": {
  "applied": ["pii_redaction", "locale_normalization", "max_length"],
  "redaction_count": integer >= 0,
  "redaction_kinds": ["email", "ssn_finnish", "phone", ...],
  "false_positive_count": integer >= 0
}
```

Closes pressure 2. Binds the sanitization-contract evidence to the
evaluation. Note that `redaction_count` is structurally separate from
`reason_codes`; a record that claims PII was redacted MUST report a
non-zero count, otherwise the receipt is inconsistent and a downstream
verifier blocks it.

### `competitor_axis_reference` (string, optional)

```
"competitor_axis_reference": "A3" | "A4" | "A6" | "A7" | "A8" | null
```

Closes pressure 3. Direct join into baseline.json's per-axis status.
The structural constraint: setting `competitor_axis_reference="A3"`
or `"A4"` does NOT upgrade the axis `claim_label` — that remains a
baseline.json field operator-gated by the counter-read tool.

### `subject_payload_size_bytes` (integer, optional)

```
"subject_payload_size_bytes": integer >= 0
```

Soft accounting field. A reviewer can detect unusually small payloads
that might be smuggling a real evaluation into a "trivial" record.
Pure observation, no policy.

### `evaluation_version` (const, required for v1)

```
"evaluation_version": "magma.evaluation_result.v1"
```

v0 readers select schema by this field; the dispatcher in v0
`_validate_evaluation_result` becomes a version-keyed switch.

## Cross-version compatibility

v0's schema declares `additionalProperties: false`. A v1 record cannot
pass v0 validation as-is. Two valid options:

**Option A — separate schemas + version-keyed dispatch (RECOMMENDED).**

- New file `schemas/v3_13_0/evaluation_result.v1.json` defining the
  superset. Required fields are v0's required set MINUS none
  (everything v0 required stays required) PLUS no new required fields
  (all v1 additions are optional).
- `waggledance/core/magma/evaluation_result.py::_validate_evaluation_result`
  becomes a dispatcher: read `evaluation_version` from the record,
  load the matching schema, validate. v0 records stay v0;
  v1 records stay v1.
- Readers that only know v0 must check `evaluation_version` themselves
  and route to a v0 validator. This is the explicit upgrade path.

**Option B — relax v0 to permit additional properties.**

- Reject for two reasons: (a) it changes v0's contract retroactively
  (existing v0 receipts would now type-check against a different
  shape than they were emitted under), and (b) it removes the strict
  fail-closed property that caught at least one tampered EvaluationResult
  in PR #598's adversarial corpus.

Going with Option A.

## F1 deliverable (this PR)

- This RFC document, NO code change.
- No schema file added yet (that lands in Phase F PR2).
- No `evaluation_version` dispatcher added yet (also F2).

## F2-F4 follow-up scope (separate PRs)

- **F2:** add `schemas/v3_13_0/evaluation_result.v1.json`, extend the
  Python builder to support v1 behind a `evaluation_version="v1"`
  caller-supplied flag, add a version-keyed validator dispatcher.
  Tests must include: v0 reader gracefully handling a v1 record by
  routing via `evaluation_version`; v1 writer producing a record that
  also satisfies the v0 required-fields subset; receipt chain
  verifying a v1 record's `target_digest` exactly like v0.
- **F3:** enumerate the sanitization-contract clauses into
  `tests/contract/sanitization_v0.py` and run them against the current
  redaction implementation. Failures become findings, never test
  weakenings.
- **F4:** companion doc `docs/architecture/SANITIZATION_CONTRACT_V0.md`
  capturing the contract verbatim with the failing-clause register.

## Open questions for RCO review

1. Is `competitor_axis_reference` the right concept, or should the
   axis attribution live ONLY in baseline.json with a per-evaluation
   join key (e.g., `case_id`)? Trade-off: in-record attribution is
   easier to audit per-receipt; baseline-only join keeps the
   evaluation record release-boundary-agnostic.
2. Should `confidence_basis.methodology_reference` be a free-form
   string or a pinned set of allowed reference patterns (e.g.,
   `rfc:<file>#<section>`)? Free-form is easier to author;
   pinned format protects against silent drift.
3. Should `sanitization_audit.redaction_kinds` be a closed enum (like
   the v0 `peer_review_trap_marker` set in the adversarial corpus
   schema), or open-ended? Closed enum guards against silent vector
   expansion; open-ended supports rivals that surface new PII kinds.

## References

- `schemas/v3_13_0/evaluation_result.v0.json` — current v0 contract.
- `waggledance/core/magma/evaluation_result.py` — current builder.
- `waggledance/core/magma/receipt.py` — receipt binding (binds
  `target_digest` and `risk_class` from the EvaluationResult).
- `tools/magma_slice_counter_read.py` — invariant guard against
  unqualified A3/A4 label upgrades.
- `docs/runs/magma_100h_sprint_2026_05_23/baseline.json` — current
  baseline with `competitor_pilot` and `receipt_adoption` blocks.
- `valiant-beaming-rocket.md` — operator-approved 100h plan; this PR
  is Phase F1.
