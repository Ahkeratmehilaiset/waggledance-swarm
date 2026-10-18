# JOIN_OUTCOME_ADAPTER v0.1: outcome rows for the JOIN card a lane actually read (contract, sandbox)

Status: FROZEN design contract v0.1, written by fable-5 for Lead's request
`lead-fable-join-outcome-contract-preparation-20261018-v1` (routing decision on task
`codex-lead-1/join-outcome-contract-preparation-20261018`). Sandbox only, advisory, never a gate. No code GO:
implementation stays held until Lead checks this frozen text and issues a separately bounded code GO. No PR, no
runtime, no real, frozen or sealed input.

## 1. Why

RCO2's outcome tool `sandbox/mind/join_card_outcomes.py` (commit b708a8325148c8ccfd19bf1f13b429b4f6287d2f, blob
099875315df54839d6aed56003325512cededbcc, contract v1.2) judges only the card `lane_view.render_text(view, lane)`
writes, byte for byte. A JOIN card (join_card v0.5) is refused there (`card_not_rendered_from_view`, every row
UNKNOWN; fable-5's synthetic probe on dbf59b69), and LEARN v1 folds those rows as no evidence. So a lane that reads
only the JOIN card gives LEARN nothing. Measuring a substituted step-1 card is not a valid proxy for what the lane
read (Lead's routing decision). This adapter judges the JOIN card itself.

This is an ADDITIVE adapter with its own label. It is not an outcome contract v1.3 (that label is not used here), it
does not change RCO2's tool, its contract or its result schema, and RCO2's source stays byte-identical with its own
author credit.

## 2. Proposed membership, imports and provenance (for the later code slice; nothing here is code)

* New paths (future, after a code GO): `sandbox/mind/join_outcome_adapter.py` and
  `sandbox/mind/test_join_outcome_adapter.py`, author fable-5.
* Imported unchanged: `sandbox/mind/join_card_outcomes.py` (author claude-rco-2; blob 09987531 carried
  byte-identical onto the card lineage: an added member and import); its functions `card_lines`, `read_time`,
  `outcome_row`, `counts`, `dedupe`, `event_pairs`, `sha256_bytes` and its constants are called, never copied or
  edited. It imports `step1/lane_view.py`, `tools/bridge_mind_workspace.py` and `tools/bridge_compact_view.py`
  (already members). It then runs against this lineage's `lane_view.py`, not its own branch's (stated limit).
* Imported from the card lineage: `join_card.py` (v0.5: `card`, `lane_records`, `see_line`, `_check_envelope`),
  `join_card_delivery.py` (`_load` style reads only), `join_turn.py` (`TURN_SCHEMA`, `OUTPUTS`).
* LEARN dependency (a separate later slice, LEARN v1.1): `learn_step._check_result` accepts only
  `wd.mind-join-card-outcomes.v1`. The adapter's result has its own schema (section 7), so LEARN folds it only after
  an explicit allowlist change plus the per-view rule of section 8. Until then the adapter result is not folded.
* Design input: this text is fable-5's. Controlled-helper Grok consultation 220d25d34a0a45dba55374f0a04a2713
  (advisory, run on fable-5's draft BEFORE Lead's request arrived; Lead verified it and ruled it the one challenge
  for this design) gave the re-render binding, the derived k, rows only from SEE, not_shown apart from every
  denominator and one scored card per lane and view. Its hidden-count formula `len(card_lines) - k` is NOT adopted:
  Lead's selected-function probe (finding on task `codex-lead-1/join-outcome-cardinality-probe-20261018`, script
  sha256 506802ff...) gave three counterexamples (N=4 records: k=1/2/3 render +3/+2/+1 more PRs), see section 5.

## 3. Inputs and the byte, cycle and lane binding

One adapter call judges one JOIN turn directory for one lane:

* `turn.json` with `schema == join_turn.TURN_SCHEMA` (`wd.mind-join-turn.v0`), `version == "join-turn-v0.3"`,
  `authority == "none"`, a `lane`, `cycle`, `as_of_utc`, `now_utc`, `outputs` holding exactly the sha256 of
  `view.json`, `delivery.json` and `card.txt`, `remember_envelope_sha256`, `learn_envelope_sha256` and
  `learn_state_sha256` (each a 64-hex string or null). Any missing, extra-typed or ambiguous field refuses.
* The three output files, read as bytes; each sha256 must equal its `outputs` entry.
* The REMEMBER envelope and the LEARN envelope the turn used, given explicitly when (and only when) the manifest's
  digest is non-null; each must hash (`lane_view.sha256_text(lane_view.canonical(x))`, the turn's own rule) to that
  digest. A missing envelope for a non-null digest, or one given for a null digest, refuses.
* The `learn_ref` string the turn passed to the card.
* RCO2's inputs, unchanged in meaning: `issue`, `receipts`, `events`, `coverage`, `head_truth`, `eligibility`,
  `identity`.

Binding (all must hold, else the card is refused, section 6):

1. `card_bytes == join_card.card(lane, delivery, view, remember, learn_ref=..., learn=...).encode("utf-8")`, re-rendered
   from the verified files and envelopes above. Byte equality binds every printed field (ci, credited, coverage,
   the REMEMBER and LEARN lines), not only the PR, head and slot.
2. `lane` equals the manifest's lane and the card header's lane; `view["as_of_utc"]` equals the manifest's and the
   header's as_of; the view's schema is `lane_view.SCHEMA`.
3. `card_sha256 = sha256(card.txt bytes)` and `view_sha256 = sha256(view.json bytes)`; RCO2's own `read_time` then
   requires the issue and every counted receipt to name exactly these two digests (no receipt of another card or
   view counts).

Source-trust boundary: these checks prove that the card bytes are what this lineage's renderer makes from these
files, and that every hash is self-consistent. They do NOT prove origin: a hand-made turn directory with consistent
hashes passes. Frozen-manifest verification is the JOIN turn's job and is only cited here (`inputs` and
`code_sha256` are copied into the result, never trusted). No identity, head or completeness is verified by the
adapter beyond what RCO2's rules already require from their own inputs.

## 4. Which lines are judged: only the SEE actions actually displayed

* `slot, records = join_card.lane_records(view, lane)` (all JOIN records with the lane's slot empty) and
  `candidates = join_card_outcomes.card_lines(view, lane)` (the view's action texts, at most `lane_view.LINE_LIMIT`
  = 3, plus `view["lanes"][lane]["more"]` cap-elided texts).
* k is DERIVED, never read from the card or the manifest. When `records` is empty, k = 0 and the card's SEE line
  must equal `join_card.see_line(view, slot, records, 0)` (the "no PR" or "no slot" line). Otherwise the SEE line
  must equal `join_card.see_line(view, slot, records, k)` for exactly one k in 1..min(3, len(records)) (the card's
  budgets show 3, 2 or 1 items). No match, or more than one, refuses.
* Item match, for i in 1..k: `records[i-1]` and `candidates[i-1]` must agree on the PR number, the FULL
  `github_head` of the record against the candidate row's `head` (RCO2's row holds the full head it verified against
  the record), the slot, and the candidate's `reason is None` (no `line_record_mismatch`); `candidates[i-1]["line"]
  == i`. Any disagreement refuses the card (no partial scoring).
* HEAR, SLOT, UNDERSTAND, REMEMBER and LEARN lines give no rows. SLOT and UNDERSTAND speak about `records[0]`, which
  is SEE item 1 when k >= 1; HEAR may name a PR outside SEE and scores nothing.

## 5. Two denominators that are never conflated

* (a) JOIN records: `len(records)`; the card shows k of them; the SEE tail `+H more PRs not shown` has
  `H = len(records) - k`. (Lead's counterexample: with 4 records and k = 1, 2, 3 the card says +3, +2, +1.)
* (b) Scored action rows: only rows 1..k of `candidates`. Candidate rows k+1..len(candidates) are listed in
  `not_shown` (line, pr, slot, head, text_sha256); the view's cap-elided texts are counted in
  `elided_by_view_cap = view["lanes"][lane]["more"]`. Hidden PRs beyond the view cap have no candidate row at all.
* `not_shown` and the elided count are outside `rows`: never UNKNOWN, NONE, taken or wrong, never in
  `counts`, and LEARN never reads them as evidence.

## 6. Refusals, absent cards and malformed input

* Absent card (no turn directory, a refused turn, no `card.txt`): no result. Nothing was shown, so nothing is scored.
* Refused card (any check of sections 3 and 4 fails, or an input is unreadable, too deep or not plain JSON): a
  result with `card = "refused: <fixed reason>"` and, as RCO2's tool does for a refused card, every candidate row
  as `UNKNOWN` with that reason, so a refused card never vanishes; `not_shown` is empty (what was shown is unknown).
  Fixed reasons: `turn_manifest_invalid`, `turn_output_digest_mismatch`, `envelope_binding_mismatch`,
  `card_not_rendered_from_turn`, `card_lane_or_as_of_mismatch`, `see_items_ambiguous`, `see_item_mismatch`,
  `view_schema_invalid`, `input_unreadable`. Messages never echo input content; every output passes the decoded
  guard (`ws.guard_object`).
* A card is never accepted by substituting the step-1 render for the JOIN card, and the step-1 path of RCO2's tool
  stays byte for byte as it is.

## 7. Rows and result

* For each shown line i (1..k): exactly RCO2's `outcome_row(candidates[i-1], lane, read, reason, issued, as_of,
  lane_events, coverage, head_truth, eligibility, identity)` with `read, reason = read_time(issue, receipts, lane,
  card_sha256, view_sha256)` and `lane_events` built as RCO2 builds them (`dedupe(event_pairs(events))`, the lane's
  own events, sorted). So UNKNOWN (receipt or issue reasons), PRE_READ, the first relevant action, coverage before
  any positive claim, head intervals versus samples (ABA), verified identity and the read window are unchanged.
* Result: `{schema: "wd.mind-join-outcome-adapter.v0.1", authority: "none", scope:
  "offline_outcome_rows_not_gate", adapter_contract_sha256, base_contract_sha256: <RCO2's CONTRACT_SHA256>, lane,
  inputs: {view_sha256, card_sha256, turn_manifest_sha256, issue_sha256, receipts_sha256, coverage_sha256,
  head_truth_sha256, eligibility_sha256, identity_sha256, events, turn_inputs, turn_code_sha256}, card: "accepted" |
  "refused: <reason>", shown_k, join_records, hidden_prs, rows, not_shown, elided_by_view_cap, counts, gap}`.
  `counts` is RCO2's `counts(rows)`: numerators and denominators over shown rows only.
* `adapter_contract_sha256` is the sha256 of this file's committed blob bytes (the freeze hash Lead receives).

## 8. Identity through issue, read, pick and outcome; denominators and duplicates

* One `card_sha256` (the JOIN card.txt bytes) everywhere: `turn.json outputs["card.txt"]`, `issue.card_sha256`,
  every counted receipt, `result.inputs.card_sha256`, and every LEARN pick record `{card_sha256, lane, shown,
  picked}` for that card. `shown` lists the REMEMBER refs the card actually rendered (the size bound may drop
  picks), recorded by the turn in a later slice, never guessed; REMEMBER refs never mix with SEE rows.
* One scored card per (lane, view_sha256): a step-1 result and a JOIN result of the same view and lane must not
  both reach LEARN (otherwise one action or one NONE counts twice). LEARN v1.1 must refuse the second; until then
  the adapter result is not folded.
* An event that several cards cite still counts once (LEARN's existing rule), and an event judged two ways stays
  no evidence (RCO2's aggregate and LEARN's `event_conflict`).

## 9. Bounded synthetic acceptance plan (all fixtures built in-test from the card tests' helpers)

Positive:
* A1 k = 3 shown, a valid build vote by the lane on line 1 inside the read window with complete coverage, a head
  interval, eligibility and verified identity: line 1 TAKEN_CORRECT, lines 2 and 3 NONE; `not_shown` empty.
* A2 the size bound forces k = 1 (long REMEMBER and LEARN content): rows only for line 1; a vote on the line-2 PR
  is not scored and line 2 is in `not_shown`; `hidden_prs == len(records) - 1`.
* A3 4 records (Lead's counterexample): `join_records = 4`, `hidden_prs = 4 - k`, `len(rows) = k`,
  `elided_by_view_cap = 1`.
* A4 unchanged semantics on shown lines: a vote before the read gives PRE_READ; head samples only give
  UNKNOWN(head_currency_unverified); an unverified identity gives UNKNOWN(identity_unverified).
* A5 the same view's step-1 render through RCO2's tool gives byte-identical result bytes before and after the
  adapter module is imported.
Negative (each a fixed refusal, never a raw error):
* N1 one byte of card.txt changed; N2 SEE items reordered; N3 a record head that differs only after 12 hex; N4 a
  card of another lane or as_of; N5 a manifest with a missing, extra or wrongly typed key, another schema or
  version; N6 an output digest mismatch; N7 an envelope missing for a non-null digest, or given for a null one;
  N8 a view of another schema; N9 an ambiguous k; N10 a receipt or issue naming the step-1 card's hash applied to
  the JOIN card (UNKNOWN by RCO2's own receipt rules); N11 too-deep or non-JSON input.
* Mutants to kill: derive k from the manifest; accept a partial SEE match; count `not_shown` in `counts`; use
  `len(candidates) - k` for hidden PRs; compare only head12; score the SLOT line; skip the envelope digests.

## 10. Tiny authoring and eligibility ledger

| Item | Author(s) | Excluded from independent review |
| --- | --- | --- |
| this contract, future adapter and its tests | fable-5 (Grok 220d25d3 advisory design input) | fable-5 |
| `join_card_outcomes.py` (imported unchanged) | claude-rco-2 | claude-rco-2 for the adapter and this delta (its own position 09:13:41Z); its JOIN-card eligibility is preserved, not declared for the stack |
| `join_card.py`, `join_turn.py`, `learn_step.py` (imported) | fable-5; RCO1 is a disclosed JOIN card-frame contributor | fable-5; RCO1 for the card-frame paths |
| cardinality counterexample used in section 5 | codex-lead-1 (peer fixture) | none by itself (a finding, not authored code) |

Lead assigns any independent probe or review only after the frozen membership and the actual contributions are
known; nothing here declares a whole-head RCO_PASS route or an operator exception.

## 11. Limits

Hash consistency is not authentication; the adapter trusts RCO2's proofs and LEARN trusts the adapter's rows; OTHER
and NONE still rest on agent labels (RCO2's limit); the adapter runs RCO2's code against this lineage's
`lane_view.py`; no utility or benefit is claimed; the item order of JOIN records and view candidates is checked per
card, not proven in general (Lead's probe found no mismatch in its fixture).
