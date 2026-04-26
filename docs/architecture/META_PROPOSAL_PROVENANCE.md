# Meta-proposal provenance

Crown-jewel area, Phase 8.5 Session D.

## Continuity anchor (every artifact)

Every artifact emitted by `tools/hive_proposes.py` carries:

```json
{
  "branch_name": "phase8.5/hive-proposes",
  "base_commit_hash": "<short sha of branch tip at run time>",
  "pinned_input_manifest_sha256": "sha256:..."
}
```

## Pinned input manifest

Recorded in `docs/runs/phase8_5_hive_session_state.json` under
`pinned_inputs[]`:

| field | meaning |
|---|---|
| `path` | absolute or relative path to the pinned file |
| `size_bytes` | size at pin time (read ceiling at emit time) |
| `mtime_epoch` | mtime at pin time |
| `sha256_first_4096_bytes` | hash of first 4096 bytes |
| `sha256_last_4096_bytes` | hash of last 4096 bytes within pinned size |
| `sha256_full` | full hash when file ≤ 8192 bytes |

`pinned_input_manifest_sha256` is the canonical sha256 of the
sorted manifest entries; the first 12 chars name the default output
directory `docs/runs/hive/<sha12>/`.

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

`inputs.validate_hook_contracts()` re-hashes each entry at run time
and rejects mismatches. The CLI's `--real-data-only` flag promotes
contract mismatches to a non-zero exit code.

## Per-artifact provenance

| artifact | provenance fields |
|---|---|
| `hive_proposals.json` | continuity_anchor + per-proposal `provenance` block (branch_name, base_commit_hash, pinned_input_manifest_sha256, consumed_hook_contracts, fixture_fallback_used) |
| `hive_proposals.md` | preamble: `human_review_boundary`, branch, base commit, pin manifest |
| `meta_evidence_map.json` | continuity_anchor + per-target proposal_id link + per-item plane / source_id / cell_id / severity / rationale |
| `review_bundle.json` | continuity_anchor + consumed_hook_contracts + per-proposal recommended_next_human_action + counts + insufficient_evidence + rejected_candidates + fixture_fallback_used + resolved_proposals |
| `review_bundle.md` | renders the JSON above with the boundary preamble at the top |
| `HISTORY.jsonl` | per-entry chain link (prev_entry_sha256), entry_sha256 (canonical sha of self-omitted entry), output_dir, base_commit_hash, pinned_input_manifest_sha256, ts |

## meta_proposal_id rule (D.txt §D6)

```
meta_proposal_id = sha256(
    canonical_json({
        "proposal_type": "...",
        "scope_class": "...",
        "impacted_cells": sorted([...]),
        "canonical_target": "..."
    })
)[:12]
```

The id is **structural** — same proposal, same id, across runs. It
deliberately excludes:
- volatile evidence refs (curiosity_id, tension_id, dream_meta_proposal_id)
- ts / mtime
- pinned_input_manifest_sha256
- branch / base_commit_hash

This stability is what enables lifecycle tracking (`new` →
`persisting` → `resolved`) across runs.

## HISTORY.jsonl chain rule

```
prev_entry_sha256(entry_n) =
    "0" * 64                       (genesis)
    or entry_sha256(entry_{n-1})   (chain link)

entry_sha256(entry) =
    sha256(canonical_json(entry without entry_sha256))
```

`history.validate_chain(entries)` walks the chain and returns the
`entry_sha256` of the first break, or `None` if intact.

## Fixture fallback documentation

D.txt §FIXTURE FALLBACK RULE allows fixture-backed inputs only with
a documented blocker. `state.json.fixture_fallback_status` mirrors
into `review_bundle.fixture_fallback_used` per plane:

```json
{
  "dream_plane": {
    "used": true,
    "reason": "Session C never executed run_dream_cycle --apply",
    "evidence": ["find docs/runs/dream/ → not found"],
    "compatibility": "fixture records conform to the schema produced by waggledance/core/dreaming/meta_proposal.py"
  },
  "r7_5_plane": {
    "used": false,
    "reason": "R7.5 evidence is OPTIONAL per D.txt §D4; missing R7.5 must NOT penalize proposals."
  }
}
```

Per `cross_plane_support_factor`, missing planes do not penalize
proposals — `num_supporting_planes` counts only contributing planes.

## Pinned input integrity check

D.txt §PINNED INPUT INTEGRITY CHECK:

| Property | Status under current code |
|---|---|
| pinned input shrank | not auto-detected; operator must re-pin |
| pinned input truncated | bounded read protects against over-reading |
| first 4096-byte hash changed | not auto-verified at run time |
| last 4096-byte hash changed | not auto-verified at run time |
| pinned input disappeared | `--real-data-only` exits 2 |

Append-only growth beyond `size_bytes` is fine — the bounded reader
caps at `size_bytes`, so newly-appended content is invisible to the
session. A future hardening (Stage 2.5) may add explicit re-hashing
at session-end.
