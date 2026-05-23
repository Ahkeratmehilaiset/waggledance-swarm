# Sanitization Contract v0

Status: Canonical contract, enforced by
`tests/contract/test_sanitization_v0.py` (Phase F PR3 of the 100h plan).
Source of truth: operator decision 4 (2026-05-10) + R20 master prompt
§2.6 placeholder names + R22.0 finding F1 (URL pre-mask). Implementation:
`waggledance/core/bridge_llm/redactor.py::BridgeLLMRedactor`.

## Goal

Cloud-bound prompts that leave the local WaggleDance runtime via the
LLM bridge MUST be redacted by default so that no PII / credential
class survives. Internal post-processing on safe paths may rehydrate
via the trusted placeholder map; cloud responses themselves MUST NOT be
rehydrated automatically.

## Clauses

Each clause has exactly one enforcement test in
`tests/contract/test_sanitization_v0.py`. Failures are findings, not
test-weakening triggers.

| # | Clause | Test |
|---|---|---|
| 1 | Email addresses are redacted to `<EMAIL_n>` | `test_clause_1_email_address_is_redacted` |
| 2 | Credit-card-like digit spans (13-19 digits) are redacted | `test_clause_2_credit_card_digit_span_is_redacted` |
| 3 | Phone-like spans (optional `+`, then 9+ digits/spaces/hyphens) are redacted | `test_clause_3_phone_number_is_redacted` |
| 4 | Finnish HETU (1900s separators) is redacted | `test_clause_4_finnish_hetu_is_redacted` |
| 4b | Finnish HETU (2000s separators A-F) is redacted | `test_clause_4b_finnish_hetu_2000s_separator_is_redacted` |
| 5 | IBAN is redacted | `test_clause_5_iban_is_redacted` |
| 6 | Y-tunnus is redacted to `<BUSINESS_ID_n>` | `test_clause_6_y_tunnus_is_redacted` |
| 7 | Windows file paths are redacted to `<PATH_n>` | `test_clause_7_windows_path_is_redacted` |
| 8 | POSIX file paths are redacted to `<PATH_n>` | `test_clause_8_posix_path_is_redacted` |
| 9 | URLs are PRESERVED verbatim (R22.0 F1) | `test_clause_9_https_url_survives_redaction` |
| 10 | Default `accept_pii_to_cloud=False` redacts | `test_clause_10_default_is_redaction_on` |
| 10b | Explicit `accept_pii_to_cloud=True` is honoured (fail-closed, no xfail) | `test_clause_10b_explicit_opt_in_preserves_pii` |
| 11 | Placeholders are numbered sequentially per call | `test_clause_11_placeholders_are_numbered_per_call` |
| 12 | Multiple classes in one call get distinct placeholders | `test_clause_12_multiple_classes_in_one_call_get_distinct_placeholders` |

Plus one smoke clause:

- **Neutral text passes through unchanged** — guards against
  over-redaction (the corpus expansion case
  `case:adv:payload_leak:004` documents the RFC 2606 example-domain
  false-positive class this clause anti-checks).

## What is NOT in the contract

- The contract does **not** require redacting names, addresses, dates of
  birth, or other "soft" PII categories. These can be added in a v1
  superset; for now they remain operator-policy concerns.
- The contract does **not** specify the exact placeholder format
  beyond `<CLASS_n>`; the redactor's internal implementation may use
  additional metadata as long as the literal PII does not survive.
- The contract does **not** require rehydration on cloud responses;
  rehydration is reserved for internal post-processing on safe paths
  only.

## Anti-claim invariants

- A failed contract test is a **finding**, not a license to weaken the
  test. If a clause flags a real over-redaction (e.g., a reserved
  domain such as `support@example.com`), the fix is in the redactor's
  classifier, not in the test assertion.
- The contract is **versioned** (`v0`). A v1 expansion would add new
  clauses as separate tests in a new file
  (`tests/contract/test_sanitization_v1.py`) so the v0 floor is never
  silently weakened by an additive change.

## Related

- `tests/contract/test_sanitization_v0.py` — enforcement.
- `waggledance/core/bridge_llm/redactor.py` — implementation.
- `docs/architecture/EVALUATION_RESULT_V1_DRAFT.md` — the EvaluationResult
  v1 RFC that introduces `sanitization_audit` as a first-class
  receipt-bound record of which clauses ran.
- `tests/fixtures/magma_adversarial_corpus/v0.json` cases
  `payload_leak:003` (multi-locale PII) and `payload_leak:004` (RFC
  2606 example-domain false-positive) — adversarial coverage of the
  contract, folded in from the Phase D expansion provenance fixture.
- Operator decision 4 (2026-05-10) — original PII redaction policy.
- R20 master prompt §2.6 — placeholder naming.
- R22.0 finding F1 — URL pre-mask requirement.
