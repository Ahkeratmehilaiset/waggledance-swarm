# v3.13.0 Operator Case Seed Bundle -- capability graph + coverage

**Bundle id**: `v3_13_0_operator_case_seed_bundle_2026_05_14`
**Companion machine-readable fixture**: `tests/fixtures/v3_13_0/operator_case_seed_bundle.json`
**Source evidence**: domain catalog at `iterations/anchor_use_case/v3_13_0_domain_catalog_claude.md` (operator read-only contract honored; no credential / token / personal correspondence content; file paths used only as evidence of a domain, not as data).

## Purpose

The unified case -> capability -> solver pipeline needs concrete operator
cases as input so that:

1. SolverSynthesizer (Sprint 2) has realistic case shapes to generate
   candidate manifests against.
2. ShadowRunner has realistic input sets to run candidates against.
3. WriteRCOGate / SolverProvenance discipline can be exercised against
   real-shaped (not toy-shaped) intents.
4. Operator-facing UX (CLI / SituationRoom) can be prototyped against
   the actual case surface, not against a hypothetical one.

This bundle is the first concrete release-pipeline input. It is sanitized
(no credentials, no personal correspondence content, no secret material)
and deterministic (sorted keys, stable identifiers, no timestamps in the
case bodies themselves).

## Coverage

18 cases across three operator profiles:

| Profile  | Case count | Cases |
|----------|------------|-------|
| home     | 11         | FIN-01, FIN-04, FIN-09, ENG-01, ENG-05, COM-01, COM-04, PROP-02, INS-01, CRY-02, MTG-01 (CRY-02 + MTG-01 are cross-profile but primarily home) |
| cottage  | 5          | FIN-10, ENG-06, ENG-07, PROP-01, BEE-01 |
| factory  | 2          | FACTORY-01, FACTORY-02 (synthetic templates; the v3_13_0 domain catalog has no committed factory-specific operator data, so these are pattern-derived) |
| (overlap)| 1          | BEE-01 lists both home and cottage in the `profiles` array |

Counts: 18 distinct case_ids. BEE-01 declares two profiles in its
`profiles` array (cottage + home) but is counted only once above (in
the cottage row, as its primary profile by intended deployment
context). Per-profile sum 11 + 5 + 2 = 18 matches the distinct-case
total. The matrix-totals row at the bottom of this document shows the
same 11/5/2 split and an 18-case grand total.

## Capability graph (top-level)

The cases collectively exercise the following capabilities. Each
capability is a SOLVER-template-level primitive that SolverSynthesizer
must be able to compose; missing capabilities are the v3.13.0 gap-list.

### Connector capabilities

* `browser_session_persistent` -- 5 cases (FIN-01, FIN-02 implicit via
  FIN-09, ENG-01, FACTORY-02, ...). The persistent browser session is the
  most-used connector primitive in the corpus; substrate has
  `tools/portal_signatures` + `*_session` folders as the operator's
  existing pattern.
* `rest_api_oauth` -- 6 cases (FIN-09, ENG-05, COM-01, COM-04, FACTORY-01, ...).
* `rest_api_no_oauth` -- 3 cases (FIN-01 partially, ENG-01 partially,
  ENG-07).
* `rest_api_no_oauth_local_lan` -- 1 case (ENG-07 cottage boiler over
  local LAN; introduces the on-site-vs-off-site reachability axis).
* `filesystem_pdf_corpus` -- 7 cases (FIN-04, FIN-10, FIN-09, PROP-01,
  PROP-02, INS-01, FACTORY-02).
* `local_sqlite_<scope>_db` -- 6 cases (FIN-04, COM-01, PROP-01,
  ENG-06, BEE-01, FACTORY-01).
* `faiss_<scope>_vector_index` -- 5 cases (FIN-04, COM-01, PROP-01,
  PROP-02, INS-01, BEE-01).
* `claude_api` + multi-provider LLM (`openai_api`, `gemini_api`,
  `grok_api`) -- 2 cases (CRY-02, MTG-01 partial). Multi-provider is a
  cross-cutting capability the operator has used (crypto_review_synthesis
  pattern).
* `weather_forecast_public_api` -- 2 cases (ENG-06, BEE-01). Predictive
  pattern for outdoor / off-site operations.
* `operator_selected_spot_price_public_feed` -- 1 case (ENG-01).
  Anchor for time-of-use energy decisions; the live provider is selected
  separately from the seed.
* `operator_review_queue` + `regulator_filing_portal_browser_session` --
  2 cases (FACTORY-01, FACTORY-02). External-effect path with explicit
  operator approval gate.

### Skill capabilities

* `document_corpus_index_faiss` -- 6 cases. Underpins all `vektoroi_*`
  retrieval flows in the existing operator workspace.
* `embedding_multilingual_e5_small` -- 2 cases (FIN-04, COM-01).
* `ocr_pdf_fallback` -- 2 cases (FIN-04, FIN-10). Required when the
  receipt corpus contains scanned PDFs from accountants.
* `sql_query_local` -- 8 cases. SQLite is the operator's default
  persistence layer.
* `time_series_aggregation_hourly` -- 2 cases (ENG-01, ENG-05).
* `time_series_aggregation_daily` -- 1 case (ENG-06).
* `time_series_aggregation_monthly` -- 1 case (PROP-01).
* `date_normalisation_iso8601` -- 1 case (FIN-01); ANTI-002 contract.
* `cross_system_id_reconciliation` -- 1 case (FIN-09). Cross-system
  matching is a known difficult skill (zettle <-> holvi mismatch
  patterns in the operator corpus).
* `cross_year_discrepancy_finder` -- 1 case (PROP-02). Multi-year audit
  pattern.
* `tag_classification_<scope>` -- 1 case (FIN-10 cottage vs home).
* `structured_term_extraction_pdf` -- 1 case (INS-01).
* `policy_clause_alignment` -- 1 case (INS-01).
* `score_weighted_ranking` -- 1 case (INS-01); SOLV-002 OfferComparator
  template.
* `evidence_citation_with_page_ref` -- 1 case (PROP-02). Citations are
  the operator's standard accountability primitive.
* `multi_model_disagreement_detection` -- 1 case (CRY-02). Cross-model
  consensus pattern; map to mutual-RCO discipline at the agent layer.
* `synthesis_with_evidence_citation` -- 1 case (CRY-02).
* `rate_limit_per_provider_budget` -- 1 case (CRY-02).
* `audio_capture_multichannel` -- 1 case (MTG-01).
* `speech_to_text_fi_en` -- 1 case (MTG-01). Language pair anchored on
  the operator's primary working languages.
* `speaker_diarization` -- 1 case (MTG-01).
* `llm_generation_streaming` -- 1 case (MTG-01).
* `sub_second_latency_budget` -- 1 case (MTG-01); cross-cutting with
  `tools/yhtiokokous/latenssi_benchmark.py` baseline.

### Write-risk distribution

* `informational` -- 7 cases (FIN-10, ENG-01, ENG-06, PROP-02, INS-01,
  CRY-02, BEE-01). Read-only decision outputs.
* `local_artifact` -- 7 cases (FIN-01, FIN-04, FIN-09, COM-01, COM-04,
  PROP-01, MTG-01). Local SQLite / FAISS / draft writes.
* `external_effect` -- 4 cases (ENG-05, ENG-07, FACTORY-01,
  FACTORY-02). All require operator approval for write per
  SolverProvenance external_effect rule (PR #377 H1+H2 + PR #379 WG2+WG3
  audit-denial discipline).

### Decision kind distribution

* `advisory` -- 11 cases. Pure recommendation surface; no write.
* `advisory_with_optional_write` -- 1 case (FIN-09).
* `advisory_with_operator_approval_for_write` -- 2 cases (ENG-05,
  ENG-07). Explicit operator approval gate before external_effect.
* `advisory_with_operator_approval_for_submit` -- 1 case (FACTORY-02).
* `advisory_with_optional_operator_alert` -- 1 case (FACTORY-01).
* `advisory_create_draft_no_send` -- 1 case (COM-04). Draft-only output
  with explicit no-send invariant.

### Failure-mode taxonomy (cross-case)

Recurring patterns across cases that v3.13.0 substrate already handles
or that Sprint 2+ must address:

| Failure mode pattern                          | Cases referencing it                                                                                                                  | Substrate handler                                                                                                                                                                  |
|-----------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| session expired / silent re-auth needed        | FIN-01, ENG-01, FACTORY-02                                                                                                            | CredentialVault revoke / status tracking + persistent browser session re-auth. CV2 (cross-restart revocation) deferred -- relevant here.                                            |
| schema drift on third-party output            | FIN-01 balance format, ENG-01 spot feed, FACTORY-02 form field, CRY-02 provider response                                              | DivergenceAnalyzer baseline-vs-candidate shadow comparison. Existing.                                                                                                              |
| credential / pii in scanned content           | FIN-04, FIN-09, INS-01 across all PDF corpus cases                                                                                    | scan_for_credential_patterns + ANTI-004 catalog (PR #382 APC1 fix). BC1 stdin PII fix (PR #384) handles consumer-side. APC2 test-fixture exclusion deferred.                       |
| rate limit / quota exhaustion                 | FIN-09 zettle, COM-04 gmail drafts, CRY-02 per-provider                                                                              | ANTI-006 rate-limit catalog enforcement. Existing.                                                                                                                                  |
| ambiguous classification                      | FIN-10 cottage-vs-home receipt, ENG-06 cottage burn vs forecast, BEE-01 swarm vs no-event                                            | DivergenceAnalyzer + INST-G09 acceptance gate; sensitive cases route through operator_review_status pending review. Existing.                                                       |
| operator approval gate for external_effect    | ENG-05, ENG-07, FACTORY-01, FACTORY-02                                                                                                | SolverProvenance operator-signature rule (PR #377 H1) + WriteRCOGate WRT-003 path (PR #379 WG2+WG3 audit-denial). Existing and locked.                                              |
| local LAN reachability when operator off-site | ENG-07 cottage boiler                                                                                                                 | Not yet handled at substrate level; future SituationRoom + connectivity policy. Sprint 2+ gap.                                                                                      |

## Smallest-first-slice pattern

Every case carries a `first_solver_slice` field that names the smallest
viable single-output operation the SolverSynthesizer should target first.
Pattern: when in doubt, the first slice is read-only, single-input,
no-write, returns one well-typed answer. Once that first slice ships
through the full pipeline (DocIngest -> manifest -> sign -> shadow ->
activate -> WriteRCOGate -> execute), the rest of the case's logic
becomes a fan-out of the same primitive.

Examples of `first_solver_slice` recurring patterns:

* "fetch X for one known Y" (FIN-01, ENG-01, ENG-05, ENG-07)
* "list X from one known scope" (COM-01, COM-04, BEE-01)
* "classify one known X" (FIN-10, FACTORY-01)
* "summarize last N items" (ENG-06, PROP-01, PROP-02)
* "extract structured field from one document" (INS-01)
* "transcribe one pre-recorded artifact" (MTG-01)
* "prefill one form, no submit" (FACTORY-02)

These first-slices are explicitly OPERATOR-FACING-VALUE-PRODUCING (each
returns one usable answer) and SHADOW-VALIDATABLE (each has a synthetic
expected output).

## Shadow_expected_output discipline

Every case carries a `shadow_expected_output` field naming the
synthetic baseline shape the operator's existing reference
implementation would produce for the first slice. These become the
ShadowRunner inputs that DivergenceAnalyzer compares the candidate
against. Pattern: synthetic data, fixed values, deterministic shape,
known anomalies. The "_3_accounts_known_fixed_values" / "_with_known_min_at_02_00_local"
suffix style names the asserted invariant inline.

## Profile coverage gaps

* **factory** -- only 2 cases (FACTORY-01, FACTORY-02), both synthetic
  templates derived from the BEE-02 regulatory pattern + PROP-05
  incident-log pattern. The operator's evidence corpus
  (`C:\Python\project`) does not contain committed factory operator data
  (operator profile_breakdown counts mention factory but the catalog
  has no FACTORY-tagged source files). Factory cases here are
  REPRESENTATIVE PATTERNS, not real-data anchors. Sprint 2+ should
  either add real factory operator data or explicitly defer factory
  profile until a real operator joins.
* **cottage** -- 5 cases. Strong representation for off-grid /
  off-site / weather-dependent decision logic.
* **home** -- 11 cases. Strong representation for the operator's
  primary workspace, where most domain catalog evidence lives.

## Risk-class coverage gaps

* No `internal_memory` case yet. The substrate handles this risk class
  but no operator workflow in the seed bundle targets it directly. This
  is acceptable -- internal_memory operations are typically substrate-
  internal (AuditLog appends, MAGMA write-through) rather than operator-
  facing first-class workflows.

## Capability gaps surfaced (Sprint 2+ deliverable backlog)

Capabilities required by cases but NOT yet in v3.13.0 substrate:

1. **operator_review_queue** -- a queue surface for external_effect
   decisions awaiting operator approval. Used by FACTORY-01,
   FACTORY-02; ENG-05, ENG-07. Currently each module emits an audit
   event but there is no unified pending-review queue.
2. **regulator_filing_portal_browser_session** -- specialized form-
   filling browser_session pattern. Used by FACTORY-02. Could be a
   subclass of `browser_session_persistent` with form-state
   serialisation.
3. **structured_form_filling** -- generic skill not yet in substrate.
4. **calendar_visit_scheduling** -- used by ENG-07, BEE-01. Substrate
   has no calendar primitive.
5. **deadline_calendar_management** -- used by FACTORY-02. Could be
   subclass of calendar_visit_scheduling.
6. **multi_model_disagreement_detection** + **synthesis_with_evidence_citation**
   + **rate_limit_per_provider_budget** -- used by CRY-02. Mirrors
   mutual-RCO discipline at the LLM layer; could be promoted to a
   first-class substrate pattern.
7. **audio_capture_multichannel** + **speech_to_text_fi_en** +
   **speaker_diarization** + **sub_second_latency_budget** -- used by
   MTG-01. The operator has existing real-time meeting tooling under
   `tools/yhtiokokous/`; integration into the v3.13.0 substrate is
   Sprint 2+ scope.
8. **language_aware_normalization_fi_en** -- used by COM-01.
   Multilingual-e5-small embedding is in substrate but normalisation
   step (Voikko etc.) is not.

## Coverage matrix (read at a glance)

```
                    | home | cottage | factory | informational | local_artifact | external_effect
--------------------+------+---------+---------+---------------+----------------+----------------
financial           |  3   |   1     |   -     |      0        |       3        |       0
energy & utilities  |  2   |   2     |   -     |      3        |       0        |       2
communications      |  2   |   -     |   -     |      0        |       2        |       0
property & cottage  |  1   |   1     |   -     |      1        |       1        |       0
insurance           |  1   |   -     |   -     |      1        |       0        |       0
crypto / multi-llm  |  1   |   -     |   -     |      1        |       0        |       0
meeting             |  1   |   -     |   -     |      0        |       1        |       0
beekeeping          | (1)  |   1     |   -     |      1        |       0        |       0
factory             |  -   |   -     |   2     |      0        |       0        |       2
--------------------+------+---------+---------+---------------+----------------+----------------
totals (cases)      | 11   |   5     |   2     |      7        |       7        |       4
```

Some cases cross-list (BEE-01 home+cottage; CRY-02 + MTG-01 are
primarily home but cross multiple capability groups). Total cases: 18.

## What this bundle does NOT include

* No credentials, tokens, or secret material (per Codex assignment).
* No personal correspondence content; only file-path references.
* No actual scan / OCR output from the PDF corpus.
* No real account numbers, transaction IDs, sender/recipient
  identifiers, or PII.
* No live or real-time data snapshots.
* No factory profile real data (the 2 factory cases are synthetic
  templates).
* No SOLVER MANIFESTS. This bundle is INPUT for the SolverSynthesizer
  pipeline. The synthesizer takes a case from this bundle + the
  operator's runbook context and emits a SCH-005 SolverCandidateManifest.
  The smoke harness at `tests/v3_13_0/test_e2e_solver_rco_smoke.py`
  (PR #387) currently hand-crafts that manifest inline; a real
  SolverSynthesizer would generate it from this bundle.

## Next concrete release-pipeline step (suggested, not implemented here)

Pick **ONE** case from the seed bundle (smallest first slice + lowest
risk class) and ship its first slice end-to-end through the existing
substrate (DocIngest -> hand-crafted manifest from seed -> sign ->
shadow -> activate -> WriteRCOGate -> execute). Suggested candidate:
**ENG-01 spot_electricity_monitor first_solver_slice =
fetch_next_24h_spot_prices_and_return_top_3_cheapest_hours**.

* informational risk class -> no operator approval needed
* synthetic shadow input exists (synthetic_24h_winter_with_known_min_at_02_00_local)
* operator-facing-value visible in one sentence
* first shipped path consumes an already-fetched local spot-price JSON;
  live provider selection remains separate
* first-slice output shape is simple (list of 3 hours)

If that slice ships in one sprint with one operator using it once on
mokki, that is the FIRST CONCRETE OPERATOR-FACING VALUE delivery for
the project.
