# JOIN_OUTCOME_ADAPTER v0.1: outcome rows for the JOIN card a lane actually read (contract, sandbox)

Status: FROZEN design contract, version marker `v0.1-r2` (revision 2 of v0.1), written by fable-5 for Lead's
request `lead-fable-join-outcome-contract-preparation-20261018-v1` and its revision request
`lead-fable-join-outcome-contract-revision-bc050b9c-20261018-v2` (task
`codex-lead-1/join-outcome-contract-preparation-20261018`). The first freeze (`v0.1`, commit bc050b9c, blob
1d124b44, sha256 2cb5f06c...) is historical and superseded by this text; section 12 lists the delta. Sandbox only,
advisory, never a gate. No code GO: implementation stays held until Lead checks this frozen text and issues a
separately bounded code GO. No PR, no runtime, no real, frozen or sealed input.

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
  `outcome_row`, `counts`, `dedupe`, `event_pairs`, `sha256_bytes`, `digest_set`, `stamp`, the private `_time` and
  its constants (`SCHEMA` is not reused; `CONTRACT_SHA256`, `HISTORICAL_GAP`) are called, never copied or edited.
  fable-5's composition probe ran its `outcomes` on this lineage once (one fixture view): the import closure loads
  there; that is not general parity. It imports `step1/lane_view.py`, `tools/bridge_mind_workspace.py` and
  `tools/bridge_compact_view.py` (already members). It then runs against this lineage's `lane_view.py`, not its own branch's (stated limit).
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

* `turn.json` as the CURRENT writer makes it (`join_turn.write_turn`, the manifest dict at lines 175-183 of
  join_turn.py on this lineage; no writer edit is implied), a JSON object with EXACTLY these 15 keys and types:
  `schema` == "wd.mind-join-turn.v0" (`TURN_SCHEMA`); `version` == "join-turn-v0.3"; `authority` == "none";
  `scope` == "sandbox turn, not a gate"; `cycle`: string (the CLI always passes `--cycle`; the library may write
  null, which the adapter refuses); `lane`: string; `as_of_utc`: string; `now_utc`: string; `inputs`: object with
  exactly `frozen_root` (string), `manifest_sha256` (64-hex), `cycle_files` (object) and `cycle_nulls` (array), or
  null (library path, refused); `receipts_given`: boolean; `remember_envelope_sha256`, `learn_state_sha256` and
  `learn_envelope_sha256`: each a 64-hex string or null, with `learn_state_sha256` null exactly when
  `learn_envelope_sha256` is null; `outputs`: object with exactly `view.json`, `delivery.json` and `card.txt`, each
  64-hex; `code_sha256`: object with exactly the `join_turn.CODE` names, each 64-hex (cited, never trusted). A
  missing key, an extra key, a wrong type or value, a bool where a string is due, or duplicate JSON keys refuse
  (`turn_manifest_invalid`).
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
* What is discriminating (r2): both sides come from the SAME view, and RCO2's row copies the head of
  `view["prs"][<its PR>]`, so once the PR numbers agree the full-head and slot equalities are implied (kept as
  assertions, not claimed as separate protection). The checks that can fail on a self-consistent but hand-made view
  are: (1) the PR order of `records` against the action texts' order (the two sorts differ in form: lane_view sorts
  by ((one-slot, waiting_since, pr), text), lane_records by (one-slot, str(waiting_since), pr)); (2) an action text
  whose head12, branch, slot or RCO/build word disagrees with its record (`line_record_mismatch`); (3) fewer action
  texts than shown items.
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

* Observable refusal boundary: every adapter call ends in exactly one of two observable results, never in silence
  or "no result": an accepted result (`card == "accepted"`, `exposure == "SHOWN_LINES_KNOWN"`) or a refusal result
  (`card == "refused: <fixed reason>"`, `exposure == "UNKNOWN"`). The CLI prints `refused: <fixed reason>` and exits
  2 on a refusal, as join_card and join_turn do.
* Absent or unreadable artifacts (no turn directory, no `turn.json`, `view.json`, `delivery.json` or `card.txt`, a
  file that cannot be read, is too deep or is not plain JSON, or an envelope that is needed but missing): a
  refusal result with reason `turn_artifact_unavailable` (or `input_unreadable` for a present but unreadable file).
  This states that exposure cannot be established. It is NOT proof that no card was shown or read: a missing record
  is UNKNOWN, never a negative.
* Refused card (any check of sections 3 and 4 fails): a refusal result with that fixed reason.
* Rows of a refusal result: when `view.json` is present, readable, matches the manifest's `outputs` digest and has
  the lane-view schema, every candidate row is listed as `UNKNOWN` with the refusal reason (as RCO2's tool does for
  a refused card, so a refused card's lines never vanish) and `not_shown` is empty (what was shown is unknown);
  otherwise `rows` is empty and `rows_unavailable == "view_unverified"`. A verified view with no action text for
  the lane gives `rows` empty and `rows_unavailable` null. UNKNOWN rows stay non-evidence in LEARN.
* Every JSON input (turn.json, view.json, delivery.json, the envelopes and RCO2's inputs) is parsed with a
  duplicate-key refusal; digests are taken over the exact file bytes. An unknown lane (not in `lane_view.LANES`)
  refuses, as RCO2's `outcomes` does.
* Fixed reasons: `turn_artifact_unavailable`, `input_unreadable`, `turn_manifest_invalid`,
  `turn_output_digest_mismatch`, `envelope_binding_mismatch`, `card_not_rendered_from_turn`,
  `card_lane_or_as_of_mismatch`, `see_items_ambiguous`, `see_item_mismatch`, `view_schema_invalid`. Messages never
  echo input content; every output passes the decoded guard (`ws.guard_object`).
* A card is never accepted by substituting the step-1 render for the JOIN card, and the step-1 path of RCO2's tool
  stays byte for byte as it is.

## 7. Rows and result

* `read, reason, receipt_reasons = read_time(issue, receipts, lane, card_sha256, view_sha256)`: RCO2's function
  returns THREE values (read time or None, the reason or None, the sorted set of reasons of receipts that failed;
  lines 185-204 of its source). `receipt_reasons` is kept in the result as `receipt_reasons_seen`, exactly as RCO2's
  `outcomes` keeps it. `issued = _time(issue["issued_utc"])` when the issue is a mapping, else None, and `as_of =
  _time(view["as_of_utc"])`, as RCO2 computes them.
* `lane_events` exactly as RCO2 builds them: `kept, event_counts = dedupe(event_pairs(events))`, then the lane's
  own events of `kept` sorted by (ts, ref). ALL of them go to every shown line's `outcome_row`; no event is dropped
  because it concerns a hidden line or another PR.
* For each shown line i (1..k): exactly RCO2's `outcome_row(candidates[i-1], lane, read, reason, issued, as_of,
  lane_events, coverage, head_truth, eligibility, identity)`. So UNKNOWN (receipt or issue reasons), PRE_READ, the
  first relevant action in the window (including a wrong-PR pass vote on the line's task, lines 400-418), coverage
  before any positive claim, head intervals versus samples (ABA), verified identity, OTHER versus NONE (any lane
  event in the window) and the read window are unchanged.
* `gap` as RCO2 sets it: HISTORICAL_GAP when there are rows, every row's reason is `receipt_missing` and there is
  no issue; else null.
* Result: `{schema: "wd.mind-join-outcome-adapter.v0.1", authority: "none", scope:
  "offline_outcome_rows_not_gate", adapter_contract_sha256, base_contract_sha256: <RCO2's CONTRACT_SHA256>, lane,
  inputs: {view_sha256, card_sha256, turn_manifest_sha256, issue_sha256, receipts_sha256, coverage_sha256,
  head_truth_sha256, eligibility_sha256, identity_sha256, events, turn_inputs, turn_code_sha256}, card: "accepted" |
  "refused: <reason>", exposure: "SHOWN_LINES_KNOWN" | "UNKNOWN", shown_k, join_records, hidden_prs, rows,
  rows_unavailable, not_shown, elided_by_view_cap, receipt_reasons_seen, counts, gap}`. On a refusal, `shown_k`,
  `join_records` and `hidden_prs` are null. `counts` is RCO2's `counts(rows)`: numerators and denominators over the
  rows only.
* `adapter_contract_sha256` is the sha256 of this file's committed blob bytes at the `v0.1-r2` freeze (the hash
  Lead receives with this revision).

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
* A2 the size bound forces k = 1 (long REMEMBER and LEARN content): exactly one row (line 1); line 2 gets NO row
  and is listed in `not_shown`; `hidden_prs == len(records) - 1`. The lane's pass vote on the line-2 PR (its own
  task) is NOT removed from `lane_events`: under the unchanged rules it is not a relevant action for line 1 (another
  task), but as a lane event inside the window it makes line 1 OTHER instead of NONE when coverage is complete. A
  second fixture puts a pass vote on line 1's task that declares the line-2 PR number first in the window, with
  complete coverage and a verified identity: line 1 is TAKEN_WRONG(pr_mismatch), as RCO2's tool would judge it.
* A3 4 records (Lead's counterexample): `join_records = 4`, `hidden_prs = 4 - k`, `len(rows) = k`,
  `elided_by_view_cap = 1`.
* A4 unchanged semantics on shown lines: a vote before the read gives PRE_READ; head samples only give
  UNKNOWN(head_currency_unverified); an unverified identity gives UNKNOWN(identity_unverified).
* A5 the same view's step-1 render through RCO2's tool gives byte-identical result bytes before and after the
  adapter module is imported.
Negative (each a fixed refusal, never a raw error):
* N1 one byte of card.txt changed; N2 SEE items reordered; N3 (r2, non-vacuous) a hand-made view, digests
  recomputed and card re-rendered from it, whose line-1 action text names another head12 than its record (or whose
  action texts are in another PR order than `records`): refused `see_item_mismatch`; N4 a
  card of another lane or as_of; N5 a manifest made by the real `write_turn` and then changed: each of the 15 keys
  removed, one extra key, each key with a wrong type (including bool for string and null where not allowed),
  another schema, version, authority or scope, a null cycle or inputs, `learn_state_sha256` and
  `learn_envelope_sha256` disagreeing in nullness, an `outputs` or `code_sha256` key set that differs, duplicate
  JSON keys; N6 an output digest mismatch; N7 an envelope missing for a non-null digest, or given for a null one;
  N8 a view of another schema; N9 an ambiguous k; N10 a receipt or issue naming the step-1 card's hash applied to
  the JOIN card (UNKNOWN by RCO2's own receipt rules, its reason kept in `receipt_reasons_seen`); N11 too-deep or
  non-JSON input; N12 each artifact absent in turn (turn.json, view.json, delivery.json, card.txt, a needed
  envelope): a refusal result with `exposure == "UNKNOWN"`, never an empty or missing result.
* Mutants to kill: derive k from the manifest; accept a partial SEE match; count `not_shown` in `counts`; use
  `len(candidates) - k` for hidden PRs; compare only head12; score the SLOT line; skip the envelope digests; drop
  the events of hidden lines before `outcome_row`; discard `receipt_reasons`; accept an extra manifest key; return
  no result for an absent artifact.

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

## 12. Revision v0.1-r2 delta (from the v0.1 freeze at bc050b9c)

Confirmed interface mismatch, fixed: section 7 of v0.1 unpacked `read_time` into two values; RCO2's immutable source
returns three (read, reason, receipt_reasons). r2 unpacks three and keeps `receipt_reasons_seen` as RCO2 does
(Lead's frozen-contract check).

Clarifications (the v0.1 text was ambiguous, not an interface mismatch):
* A2: "a vote on the line-2 PR is not scored" now means only that the hidden line gets no row; every lane event
  still reaches `outcome_row` for the shown lines, so the first relevant action (also a wrong-PR vote on the
  shown line's task) and OTHER versus NONE stay exactly RCO2's.
* Section 3 and N5: the complete current `write_turn` key set and types (15 keys, including `scope`, `inputs`,
  `receipts_given` and `code_sha256`), so an extra key can be refused; no writer edit.
* Section 6: an absent or unreadable artifact gives an observable refusal result with `exposure == "UNKNOWN"`
  (`turn_artifact_unavailable`), never "no result", and it never claims that nothing was shown.

Own refinements in r2 (not raised by Lead's check; written before Tools' parallel feasibility result, which had not
arrived at this freeze): the full-head and slot equalities of section 4 are implied by the shared view record and
no longer counted as separate protection; N3 is replaced by a non-vacuous hand-made-view case; empty candidates
are accounted; JSON duplicate keys and unknown lanes refuse; the imported RCO2 names include `digest_set`, `stamp`
and the private `_time`.

Unchanged and kept: the two cardinalities of section 5 (Lead's counterexample), the re-render binding, the derived
k, rows only for displayed SEE actions, RCO2's source byte-identical, no outcome v1.3 label, no code GO. Open
limits stay in section 11. Ledger addition: Lead's frozen-contract check (finding on this task) is review input
for r2, not authored text.
