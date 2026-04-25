# Dream pipeline provenance

Crown-jewel area, Phase 8.5 Session C.

## Continuity anchor (every artifact)

Every dream artifact carries a `continuity_anchor` block:

```json
{
  "branch_name": "phase8.5/dream-curriculum",
  "base_commit_hash": "...",
  "pinned_input_manifest_sha256": "sha256:...",
  "replay_manifest_sha256": "sha256:... | null"
}
```

## Pinned input manifest

Recorded in `docs/runs/phase8_5_dream_session_state.json` under
`pinned_inputs`:

| field | meaning |
|---|---|
| `path` | relative or absolute path to the pinned file |
| `size_bytes` | size at pin time |
| `mtime_epoch` | mtime at pin time |
| `sha256_first_4096_bytes` | hash of first 4096 bytes |
| `sha256_last_4096_bytes` | hash of last 4096 bytes within pinned size |
| `sha256_full` | full hash when file ≤ 8192 bytes |

`pinned_input_manifest_sha256` is the canonical sha256 of the sorted
manifest entries.

## Hook contracts

`state.json.consumed_hook_contracts[]` lists every contract this
session depends on:

```json
{
  "file": "docs/architecture/HOOKS_FOR_DREAM_CURRICULUM.md",
  "version": 1,
  "file_sha256": "sha256:..."
}
```

`meta_proposal.validate_hook_contracts()` re-hashes the file at emit
time and rejects mismatches.

## Per-artifact provenance

| artifact | provenance fields |
|---|---|
| `dream_curriculum.json` | continuity_anchor, primary_source, secondary_fallback_reason, counts_by_mode/kind |
| `dream_request_pack_night_NN.json` | continuity_anchor, **dream_request_pack_sha12** (sha256 of pack-without-sha, truncated to 12), source_tension_ids, source_curiosity_ids, replay_case_ids, structural_context, mode, primary_source |
| `replay_case_manifest.json` | continuity_anchor + source_path, MAX_REPLAY_CASES cap |
| `proposal_collapse_report.json` | continuity_anchor, max_proposals_effective, gate_provenance per proposal, linkage_source / linkage_pack_sha12 per proposal, truncated_proposals[] |
| `shadow_replay_report.json` | continuity_anchor, replay_methodology = `structural_proxy_v0.1`, replay_methodology_disclaimer, all replay metrics |
| `dream_meta_proposal.json` | continuity_anchor + consumed_hook_contracts[], source_tension_ids, selected_proposal block (path/id/solver_name/cell_id/solver_hash), gate_provenance, collapse_results, replay_metrics, structural_gains, expected_value_of_merging, confidence, uncertainty, why_human_review_required, why_runtime_flip_is_out_of_scope |

## dream_request_pack_sha12 rule

```
sha12(pack) = sha256(canonical_json(pack with sha12 field omitted))[:12]
```

- compute LAST
- write LAST
- never include the sha field itself in the hash input
- proposal sidecar's `responding_to_dream_request_pack_sha12` MUST
  exactly equal this value

## Linkage rule (proposal ↔ pack)

Sidecar file `<proposal>.json.dream_metadata.json`:

```json
{
  "responding_to_dream_request_pack_sha12": "string",
  "generation_method": "manual | llm | unknown",
  "external_model_hint": "string or null"
}
```

Proposals without a matching pack reference (sidecar or schema-allowed
inline) are REJECT_HARD as orphans.

## Solver hash

`meta_proposal.solver_hash_for_proposal` is the deterministic
sha256 of `(cell_id, solver_name, inputs, outputs,
formula_or_algorithm)` in canonical JSON. Used for cross-session
correlation; not a substitute for the runtime axiom hash.

## Gate provenance (per c.txt §C4)

Each gate result records exactly one of:

- `native` — actual Phase 8 propose_solver gate
- `shadow_proxy` — Session C deterministic proxy
- `unavailable` — gate could not be run

The aggregate `gate_provenance_summary` keeps the worst-case across
proposals.
