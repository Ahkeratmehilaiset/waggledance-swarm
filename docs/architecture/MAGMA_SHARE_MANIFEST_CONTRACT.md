# MAGMA Share Manifest Contract

`magma.share_manifest.v0` is the contract-first boundary for cross-instance
MAGMA sharing. Runtime export remains disabled by default; the only writer in
this repo is an explicit operator-gated local exporter.

The manifest is for peers that need to review or replay MAGMA evidence without
receiving private prompts, raw payloads, raw solver outputs, replacement maps,
or deterministic raw-query digests. It can name receipt and EvaluationResult
digests, counts, gates, verdicts, and sanitization facts. It must not include
payload files or payload digests.

## Required Boundary

- `runtime_export_enabled` is always `false` in v0.
- `export_policy.contract` is `sanitization_v0`.
- `export_policy.payload_visibility` is `no_payload`.
- `allow_payload_digests`, `allow_raw_payloads`, `allow_replacement_maps`,
  `allow_raw_context`, `allow_raw_solver_outputs`, and
  `allow_deterministic_query_digests` are all `false`.
- `artifact_counts.payload_files` is `0`.
- `forbidden_material_absent` and each entry's
  `sanitization.raw_material_removed` must list:
  `raw_payload`, `replacement_map`, `raw_context`, `raw_solver_output`, and
  `raw_query_digest`.

Allowed entry fields are limited to stable MAGMA references and review state:
entry id, receipt digest, EvaluationResult digest, subject type, risk class,
expected/actual gate, verdict, and a sanitization summary. The local exporter
validates this schema with date-time format checks and count-consistency checks
before writing any share artifact.

All `share_id` and `producer` values are identifiers, not secret-bearing
fields. Their reference-shape validation is not a privacy sanitizer: operators
must supply opaque, non-sensitive refs and must never place paths, credentials,
private payload markers, or personal data in them.

## Operator-Gated Exporter

`tools/export_magma_share_manifest.py` converts an already verified local
MAGMA receipt-bundle `manifest.json` into a payload-free `share_manifest.json`.
It requires an explicit `--operator-approval-id`; the approval ref is checked
for shape but is not written to the export report. The tool writes only:

- `share_manifest.json`
- `share_export_report.json`

It does not copy payload files, does not export `canonical_payload_digest`, does
not export replacement maps, and does not enable default runtime receipt
emission.

Example:

```powershell
python tools\export_magma_share_manifest.py `
  --source-manifest <receipt-bundle>\manifest.json `
  --out-dir <new-share-export-dir> `
  --operator-approval-id operator:approval:example `
  --share-id magma:share:example:001 `
  --producer-agent codex-lead-1 `
  --producer-role lead `
  --bridge-event-ref bridge:example `
  --purpose cross_instance_replay `
  --json
```

## No-Authority Importer

`tools/import_magma_share_manifest.py` verifies a received
`share_manifest.json` as replay metadata only. It requires a local
receipt-bundle `manifest.json` so the receiver can prove the share manifest's
receipt and EvaluationResult digests still match the review context. The
importer rejects stale manifests and context drift, including a changed source
manifest digest or changed per-entry receipt/EvaluationResult references.
Every successful import also requires receiver-owned expectations for
`share_id`, `purpose`, and the complete producer triplet (`agent_id`, `role`,
and `bridge_event_ref`). The receiver must obtain those values independently;
it must not derive them from the received manifest.

The importer does not copy payload files, does not reconstruct a receipt
bundle, does not grant runtime authority, and does not enable runtime export.
Its report contains a no-authority replay plan with digest and categorical
review fields only.

When an operator wants to hand a verified import to a peer-review lane, the
same CLI can write a local `share_import_peer_review_handoff.json` artifact via
`--peer-review-handoff-out` into an existing directory. That handoff requires
an operator decision ref, operator agent ref, and bridge event ref. It records
the import decision, digest bindings, replay metadata refs, and
authority/privacy flags, while redacting the operator decision id and recording
no local paths.

## Replay Admission Contract Surface

The importer now emits `magma.share_manifest_import.v1` with
`magma.share_manifest_replay_admission_contract.v1` as the admission gate for
any future multi-instance replay flywheel. The received share-manifest schema
remains v0. Successful peer-review handoffs and admission-status summaries are
also v1 and carry the admission-contract version and digest. A receiver must
treat a share manifest as replay metadata only until all of these checks pass:

- The input validates as `magma.share_manifest.v0`.
- The receiver independently configured the expected `share_id`, `purpose`,
  producer agent, producer role, and producer export-event ref.
- `purpose` is the expected review purpose, normally
  `cross_instance_replay`.
- The received producer triplet exactly matches the receiver-owned
  expectation.
- A domain-separated canonical digest of that expectation is bound into the
  admission contract as `expected_producer_provenance_digest`; raw producer
  refs are not copied into the report, summaries, handoff, or bridge template.
- `export_policy.contract` is `sanitization_v0`.
- `export_policy.payload_visibility` is `no_payload`.
- Every payload/raw-material allowance remains `false`.
- `artifact_counts.payload_files` is `0`.
- `forbidden_material_absent` and each entry's
  `sanitization.raw_material_removed` include `raw_payload`,
  `replacement_map`, `raw_context`, `raw_solver_output`, and
  `raw_query_digest`.
- `created_at_utc` is UTC, not in the future beyond the importer clock-skew
  tolerance, and within the receiver's max-age window. The configured window
  must be an integer from 1 through 168 hours.
- `sanitized_source_manifest_digest` still matches the separately supplied
  local receipt-bundle `manifest.json`.
- Each entry's receipt digest, EvaluationResult digest, subject type, risk
  class, gate decisions, and verdict still match the local receipt/evaluation
  pair.
- The v1 import report, artifact counts, replay plan, and replay entries are
  closed shapes. Unknown fields and non-schema categorical values are rejected
  before any downstream report/replay digest or handoff can commit or copy
  them.

Schema validity proves shape, not producer authenticity. Receiver pinning
closes substitution against a known expected producer, but v1 is still
unsigned: it provides no cryptographic identity proof and does not authorize
transport. A future signed transport contract must use a real verifier rather
than a producer-supplied `signed=true` flag.

Persisted v0 import reports, handoffs, and admission-status summaries are not
silently upgraded. They must be re-imported from the verified local source
manifest with explicit receiver-owned identity and producer expectations.
Stripping the nested admission contract or recomputing its digest is a
fail-closed downgrade, not a legacy fallback.
The handoff-status consumer also recomputes each v1 handoff's canonical digest
and verifies the digest-derived handoff id. Handoff root, ownership, authority,
privacy, replay-plan, and entry objects are closed shapes with canonical
categorical values. These checks detect stored handoff mutation and prevent
unknown-field digest commitments, but remain unsigned validation rather than
producer authentication.

The only successful admission output is a local report or optional
operator-owned peer-review handoff. It must keep:

- `replay_metadata_only` true.
- `no_authority_import` true.
- `runtime_export_enabled`, `runtime_authority_granted`, and
  `runtime_authority_changed` false.
- `payload_files_imported` as `0`.
- `payload_digest_imported`, `raw_material_imported`, and
  `replacement_map_imported` false.
- Local paths, operator decision ids, payload text, raw context, raw solver
  outputs, replacement maps, and deterministic raw-query digests out of the
  serialized handoff.

For reviewer-facing observability, `--admission-status-json` emits a compact
path-free admission-status object instead of the full import report. A ready
status records the report digest, admission-contract digest, share/source
manifest digests, entry count, age, context state, and the same no-authority /
privacy flags. It does not include the full replay plan, local paths, payload
material, transport, or runtime authority.

`tools/build_magma_share_import_admission_status_bridge_event_template.py` can
turn that admission-status object into a bridge-event template for reviewers.
It is template-only: it does not append to the bridge, enable transport, import
payload files, record local paths, or grant runtime authority. Ready admissions
render as a `handoff` template; rejected or blocked admissions render as a
`finding` template with a blocker class and digest-only context. A ready
template requires the canonical v1 category values, all four SHA-256 evidence
digests, strict counts, verified/no-drift context flags, no controls, and the
operator-handoff requirement.

`--replay-sanitization-summary-json` emits the next narrower replay
sanitization contract view. It records the manifest version, admission-contract
version/digest, sanitization contract (`sanitization_v0`), required-check
count, rejection-mode count, report invariants, redaction-inventory count,
entry count, and digest bindings. It deliberately omits required-check names,
redaction-inventory entries, the full `replay_plan`, per-entry ids, per-entry
receipt/EvaluationResult digests, local paths, payload material, transport, and
runtime authority. Rejected imports still exit nonzero and emit the same
path-free no-authority flags plus a blocker class.

`tools/build_magma_share_import_replay_sanitization_bridge_event_template.py`
turns that replay-sanitization summary into a bridge-event template for
reviewers, the sibling of the admission-status template. It is template-only: it
does not append to the bridge, enable transport, import payload files, export the
replay plan or entry ids, record local paths, or grant runtime authority. Ready
summaries render as a `handoff` template; rejected or blocked summaries render as
a `finding` template with a blocker class. The template echoes only digests,
counts (entry, required-check, rejection-mode, redaction-inventory, and
report-invariant), the sanitization contract/scope, and the same
no-authority/privacy flags -- never the required-check names, redaction-inventory
entries, replay plan, or per-entry ids. It is a bounded first layer with no
`_index_entry` / `_verification_summary` recursion.

When `--json` is set and admission is rejected, the importer still exits
nonzero and writes the human failure line to stderr, but stdout contains a
sanitized admission-status JSON object. `--admission-status-json` uses the same
fail-closed stdout shape for rejected admissions. That failure status records
the contract digest, a `blocker_class` from the admission contract's
`rejection_modes.reason_code` values, and the same no-authority/privacy flags
as a successful report. It must not echo local paths, operator decision ids,
payload text, raw context, raw solver outputs, replacement maps, deterministic
raw-query digests, or private marker strings.

A receiver must reject the admission attempt on schema failure, missing
sanitization inventory, any relaxed payload/raw-material flag, stale or future
timestamps, missing receiver identity/provenance expectations, share-id,
purpose, or producer-provenance mismatch, producer binding drift,
source-manifest digest drift, per-entry digest drift, categorical context
drift, failed local source receipt verification, unsafe source-manifest paths,
or a failed import report being handed to peer review. Public blocker classes
remain generic for the producer triplet so rejection output cannot become a
field-by-field probing oracle.
If a supposedly successful import produces a blocked admission or replay
summary, the CLI also exits nonzero; callers must not treat JSON emission alone
as acceptance.

Example:

```powershell
python tools\import_magma_share_manifest.py `
  --share-manifest <share-export-dir>\share_manifest.json `
  --source-manifest <receipt-bundle>\manifest.json `
  --expected-share-id magma:share:example:001 `
  --expected-purpose cross_instance_replay `
  --expected-producer-agent codex-lead-1 `
  --expected-producer-role lead `
  --expected-producer-bridge-event-ref bridge:example `
  --max-age-hours 168 `
  --admission-status-json
```

Full report plus optional peer-review handoff:

```powershell
python tools\import_magma_share_manifest.py `
  --share-manifest <share-export-dir>\share_manifest.json `
  --source-manifest <receipt-bundle>\manifest.json `
  --expected-share-id magma:share:example:001 `
  --expected-purpose cross_instance_replay `
  --expected-producer-agent codex-lead-1 `
  --expected-producer-role lead `
  --expected-producer-bridge-event-ref bridge:example `
  --max-age-hours 168 `
  --peer-review-handoff-out <existing-dir>\share_import_peer_review_handoff.json `
  --operator-decision-id operator:decision:example `
  --operator-agent operator:wd-image1 `
  --bridge-event-ref bridge:example `
  --json
```

## Non-Goals

This contract does not make MAGMA append-only by default, does not export full
receipt bundles, does not add cross-instance transport, and does not grant any
runtime authority. The exporter emits a local metadata artifact only, and the
importer consumes that artifact only as local no-authority replay metadata. The
peer-review handoff is also a local operator-owned record; it is not an
activation approval, transport mechanism, or runtime mutation path.
Receiver pinning is not a signature, certificate, instance attestation,
anti-replay nonce, transport implementation, or proof that two distinct
instances performed a replay.

## Validation

Run:

```powershell
python -m pytest tests\contracts\test_magma_share_manifest_schema.py -q
python -m pytest tests\tools\test_magma_share_manifest_exporter.py -q
python -m pytest tests\tools\test_magma_share_manifest_importer.py -q
```

The regression tests prove the good fixture validates and that raw payloads,
payload digests, replacement maps, raw context, raw solver output, raw-query
digests, payload files, invalid timestamps, and runtime export enablement are
rejected. The exporter tests prove that source receipt bundles are verified
fail-closed, an operator approval ref is required, artifact counts must match,
and private payload markers do not appear in exported JSON. The importer tests
prove that fresh share manifests build no-authority replay plans, stale
manifests are rejected, context-drifted receipt references are rejected, source
receipt verification remains fail-closed, operator-owned peer-review handoffs
record import decisions without runtime authority, receiver expectations cannot
self-bind to received identity/provenance, schema-valid producer substitution is
rejected, and CLI output does not echo private payload markers or local paths.
