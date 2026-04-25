# Dream Mode 2.0

Phase 8.5 Session C, crown-jewel area `waggledance/core/dreaming/*`.

## Layered architecture (c.txt §DREAM PIPELINE FOCUS)

| layer | role | runtime touch | code |
|---|---|---|---|
| 1. stochastic generation | external LLM produces proposals | OUT-OF-PROCESS only | `docs/prompts/dream_teacher_prompt.md` |
| 2. deterministic collapse | proposals run through Phase 8 gates | offline | `dreaming/collapse.py` |
| 3. shadow graph + replay | structural counterfactual | in-memory only | `dreaming/shadow_graph.py`, `dreaming/replay.py` |
| 4. meta-proposal | shadow-only recommendation | none | `dreaming/meta_proposal.py` |

**Strict invariants:**

- no port 8002 touch
- no Ollama / network calls in dreaming code
- no axiom YAML writes
- no runtime registration
- shadow-only by spec; `--shadow-only=false` is warned and ignored
- crown-jewel BUSL Change Date pinned at `2030-03-19`

## Pipeline

```
state.json + pinned inputs
        │
        ▼
dream_curriculum.py        ──► dream_curriculum.{json,md}      (7 nights)
        │
        ▼
build_request_pack         ──► dream_request_pack_night_NN.{json,md}
        │
        ▼
external LLM (out-of-band) ──► proposal_NNN.json + proposal_NNN.json.dream_metadata.json
        │
        ▼
collapse_many              ──► proposal_collapse_report.{json,md}
        │
        ▼
build shadow_graph + diff
select replay cases        ──► replay_case_manifest.json
build_replay_report        ──► shadow_replay_report.{json,md}
        │
        ▼
build_meta_proposal        ──► dream_meta_proposal.{json,md}   (only if structurally promising)
```

## Continuity / provenance

Every dream artifact carries:

- `branch_name`
- `base_commit_hash`
- `pinned_input_manifest_sha256`
- `replay_manifest_sha256`
- `consumed_hook_contracts[]` (file + version + file_sha256)
- `dream_request_pack_sha12` (for request packs)
- `solver_hash` (for meta-proposals)

## Determinism contract

- canonical JSON: sorted keys, compact separators where applicable
- no time-based or random fields in core artifact bodies
- byte-identical on re-run for fixed pinned inputs and proposal fixtures
- replay selection bounded: `MAX_REPLAY_CASES = 50`

## Why this is safe during the live campaign

- No code path writes to authoritative solver roots.
- No code path imports the runtime registry.
- All emitted artifacts live under `docs/runs/dream/<sha12>/`.
- Meta-proposal explicitly states `why_runtime_flip_is_out_of_scope`.
- The dream teacher prompt forbids cross-cell fabrication, hidden
  side channels, and fabricated metrics.

## Deferred to a later session

- live execution-based counterfactual replay
- runtime promotion of an `ACCEPT_CANDIDATE`
- meta-learner / `hive_proposes` integration
- automatic post-replay merge
