# Gap miner — first curiosity organ

- **Status:** Phase 8.5 Session A landing. Offline-only. Read-only over disk artifacts. Runtime untouched.
- **Source of advice:** x.txt Phase 8.5 Session A spec.
- **Companion docs:** `HONEYCOMB_SOLVER_SCALING.md` (why scaling needs curiosity), `MAGMA_VECTOR_STAGE2.md` (vector pipeline that consumes teacher proposals later).

## 0. Why this exists

The Phase 8 scaffolding gave the teacher loop everything except a teacher signal. Cell manifests, hash, proposal gate, composition graph, hex subdivision plan, vector indexer — none of it tells the teacher what's *worth* proposing next. It says only "here's what would be valid if you proposed it."

The 400 h campaign generates ~50 k query results, ~15 k hybrid retrieval traces, ~2 k incidents. That data is a gold mine for "what is this system bad at right now," but it sits as raw JSONL with no analysis layer. Reading it by eye doesn't scale; reading it with a regex misses the structural signals (cell diversity, subdivision pressure, bridge candidates).

`gap_miner.py` is the analysis layer. It turns raw campaign data into a deterministic, ranked list of curiosity items the teacher loop can act on.

## 1. What it actually does

A run consists of seven steps, all pure functions, all deterministic given a pinned input set:

1. **Discover** the latest `docs/runs/ui_gauntlet_400h_*` directory or use a CLI-supplied `--campaign-dir` / `--fixture` root.
2. **Pin** every input artifact's `(byte_count, line_count, sha256)` at session start. The combined `pin_hash` becomes part of every emitted file so two runs against the same pin produce byte-identical output. A live-growing `hot_results.jsonl` does **not** contaminate today's run with tomorrow's rows because reading is bounded to the pinned byte limit.
3. **Read** JSONL up to the byte limit; tolerate malformed lines; join `hot_results.query_id` to `query_corpus.json` to recover the actual query text.
4. **Cluster** queries by stable token-signature: lowercased, alphanumeric-only, top-6 longest tokens sorted alphabetically and joined as `_token1_token2_...`. Same query → same signature, no randomness, no embedding model required.
5. **Detect gap type** per cluster using a deterministic heuristic (eight types from x.txt §A1.8: missing_solver, improvement_opportunity, bridge_composition, unit_family_mismatch, contradiction_surface, low_confidence_routing, subdivision_pressure, meta_solver_opportunity).
6. **Rank** by `estimated_value` — a function of count, fallback rate, p95 latency, cell diversity, and gap-type-specific structural bonuses. Critically NOT just count: a small cluster with a strong subdivision-pressure signal can outrank a frequent missing-solver cluster.
7. **Emit** four artifact families: a JSON summary, a Markdown report, a JSONL event log, and per-cell teacher packs. Every artifact is byte-stable across reruns of the same pin.

## 2. Why it's more than a report

A frequency report ranks rows by count. A curiosity organ ranks them by *expected value of acting on them*. Concretely:

- A 700-occurrence cluster with `fallback_rate = 0.0` and `p95 < 1500 ms` is **not** a curiosity. It's a working solver. The miner classifies it `do_nothing` even though it's the loudest in the data.
- A 5-occurrence cluster spanning three cells with weather-related vocabulary IS a curiosity. The miner classifies it `propose_bridge` because the structural evidence (cell diversity) carries weight that frequency alone wouldn't.
- A 50-occurrence cluster in a cell whose `hex_subdivision_plan.md` severity is 5.5 outranks a 50-occurrence cluster in a cell at severity 1.0 — same count, different evidence weight.

A curiosity item carries the recommended next action, not just the symptom: `propose_solver` / `improve_solver` / `propose_bridge` / `propose_subdivision` / `clarify_routing` / `propose_meta_solver` / `do_nothing`. The teacher loop reads this directly; no human has to translate "this looks like an LLM fallback cluster" into "build a solver here."

## 3. Higher-order curiosity types

The contract supports more than missing-solver entries. Concrete examples emitted today:

| `suspected_gap_type` | Triggers | Recommended action |
|---|---|---|
| `missing_solver` | fallback_rate ≥ 0.5 | `propose_solver` |
| `improvement_opportunity` | resolved-but-slow (p95 ≥ 8000 ms) | `improve_solver` |
| `bridge_composition` | cluster spans ≥ 2 cells, low fallback | `propose_bridge` |
| `subdivision_pressure` | dominant cell has severity ≥ 5.0 | `propose_subdivision` |
| `contradiction_surface` | error rate dominates the cluster | `clarify_routing` |
| `low_confidence_routing` | cell unattributed but signal exists | `clarify_routing` |
| `meta_solver_opportunity` | bucket-diverse mid-evidence cluster | `propose_meta_solver` |
| `unit_family_mismatch` | reserved for Session B's unit-aware scan | `propose_bridge` |

A low-evidence cluster in any non-subdivision-pressure category is automatically downgraded to `do_nothing`. The teacher should NOT be flooded with shallow signals.

## 4. Ranking is deterministic and explainable

The miner uses the x.txt §EXPECTED VALUE FORMULA exactly as specified:

```
expected_value =
    count
    × fallback_rate
    × evidence_strength
    × (1 + bridge_candidate_bonus)
```

Where:
- `count` = cluster case count (`response_count + no_response_count`)
- `fallback_rate` = `no_response_count / count`, clamped to `[0, 1]`
- `evidence_strength` = normalized weight from a fixed table:
  - `low` (count < 3) → `0.3`
  - `medium` (count 3–7) → `0.6`
  - `high` (count ≥ 8) → `1.0`
- `bridge_candidate_bonus` = `0.25` when the cluster has any
  `bridge_candidate_refs`, else `0.0`

A cluster that always succeeds ranks at exactly `0.0` and is filtered
out of the curiosity stream — that is intentional. A working solver
is not curious. Subdivision-pressure and improvement-opportunity
boosters are surfaced via `gap_type` classification (which routes to
ACCEPT-class actions like `propose_subdivision` or `improve_solver`)
rather than by inflating the ranking score.

Reviewers can second-guess one weight at a time. The miner does not
learn weights; this is intentional. A learned ranker is a separate
Session B / C scope and would couple the curiosity organ to a
feedback signal that does not yet exist.

### Cluster identity is content-derived, not positional

Per x.txt §CLUSTERING DETERMINISM RULE, `cluster_id` is computed as:

```
cluster_id = "cl_" + sha256(
    "|".join(sorted(member_query_hashes))
)[:12]
```

Where `member_query_hashes` comes from `_query_hash(query)` —
sha256 of the lowercased, alphanumeric-stripped query, truncated
to 16 hex characters. **Sequential cluster numbering is forbidden.**
This means: two runs that emit clusters in different iteration
orders still produce the same `cluster_id` for the same logical
member set, which lets downstream consumers (teacher loop,
audit trail) carry the id forward across reruns.

## 5. How the output later feeds the teacher loop

The `teacher_packs/<cell>.json` artifact is the direct input contract for `tools/propose_solver.py`'s teacher path. Each pack carries:

- a `pin_hash` so the teacher's proposal can later be audited as "this proposal was made in response to that exact campaign-data snapshot"
- per-curiosity `teacher_input_payload` containing the gap_type, candidate_cell, query examples, evidence count, and fallback count
- `recommended_next_action` so the teacher's prompt is conditioned on the structural diagnosis, not just the symptom
- `bridge_candidate_refs` and `subdivision_pressure_hint` so the teacher knows whether the gap is solver-shaped or topology-shaped

The eventual `dream_curriculum` (deferred to Session B/C) will read the same packs to decide which clusters dream-mode should rehearse overnight. The contract is identical because the curiosity organ is the single source of truth for "what is interesting next."

## 6. Why this is safe during a live campaign

Five guarantees, all enforced by code or test:

- **Read-only.** No write to `data/faiss_staging/`, `data/faiss_delta_ledger/`, `data/audit_log.db`, or any campaign artifact. Only reads.
- **No port 8002.** The miner does not import any runtime adapter or open any network socket.
- **No Ollama dependency.** Clustering is pure n-gram / token-signature; tests run in <1 second on any machine.
- **Pinned artifact set.** Live-growing `hot_results.jsonl` is bounded to the byte count captured at session start; new rows added during the run are ignored. The pin records, per artifact: `bytes`, `lines`, `mtime_epoch`, `sha256_first_4096`, `sha256_last_4096`, and `sha256_full`. The combined `pinned_artifact_manifest_sha256` is what every emitted curiosity carries in its `continuity_anchor`.
- **Deterministic output.** Every emitted artifact family has a byte-identity test under the same pin (`tests/test_gap_miner.py`). If a future change accidentally introduces non-determinism, those tests fail before merge.

### Pinned-artifact integrity check

`verify_pin_integrity()` runs at session end (or on demand) and re-hashes the pinned window of every artifact:

| Outcome | Status | Critical? |
|---|---|---|
| Same bytes, same first/last hashes | `ok` (with `growth_bytes` if file grew append-only) | no |
| File missing | `missing` | yes |
| File shrunk | `shrunk` | yes |
| First 4 KB hash drifted | `first_drift` | yes |
| Last 4 KB within pinned size drifted | `last_drift` | yes |

Append-only growth past the pinned size is the expected case during a live campaign and is **not** flagged as critical. Mutation of any byte within the pinned window is a critical failure that aborts further trust in the run.

### `continuity_anchor` on every emitted item

Every `CuriosityItem` carries:

```json
"continuity_anchor": {
  "branch_name": "phase8.5/curiosity-organ",
  "base_commit_hash": "59966b0…",
  "pinned_artifact_manifest_sha256": "sha256:abc…"
}
```

This is what lets a Session B / Session C consumer (teacher loop, dream curriculum) prove "this proposal was generated in response to that exact data snapshot at that commit on that branch."

## 7. What is intentionally deferred

- **Embedding-based clustering** (faiss-cpu is available but adds external dependence + non-trivial test surface). The token-signature clusterer is good enough for Session A and stays deterministic. Session B can add an embedding pass.
- **Learned ranker.** No training, no feedback loop. Session A's value is a stable contract; learned weights belong after the contract has been used in anger.
- **Live counter integration.** The miner does not push anything to `/metrics`. Adding live counters depends on the campaign-end runtime repoint per `MAGMA_FAISS_SCALING.md`.
- **Cross-artifact deduplication.** If the same query text shows up in both `hot_results.jsonl` and `magma_hybrid_candidate_trace.jsonl`, today it can be counted twice. The fix lives in Session B once the magma trace's role is settled.
- **Schema-versioned upgrade path** for the `CuriosityItem` dataclass. v1 ships now; v2 lands when the teacher loop has actual usage feedback.
- **Per-curiosity `uncertainty_signature`** beyond what `latency_signature` already captures. Session A leaves this field as `None` rather than fabricate a number.

## 8. CLI cheat-sheet

```
# Dry-run summary against the latest campaign
python tools/gap_miner.py

# Validate pin only (x.txt §CLI REQUIREMENTS --dry-run)
python tools/gap_miner.py --dry-run

# Full apply against the latest campaign — writes to
# docs/runs/curiosity/<pin_sha12>/ by default
python tools/gap_miner.py --apply

# Reproduce a previous session's pin from its state.json
python tools/gap_miner.py --artifact-manifest \
    docs/runs/phase8_5_curiosity_session_state.json --apply

# Specific campaign root
python tools/gap_miner.py --campaign-dir docs/runs/ui_gauntlet_400h_20260413_092800 --apply

# Fixture-backed run for testing / development
python tools/gap_miner.py --fixture tests/fixtures/gap_miner_sample/ --apply

# Filter to one cell + minimum value threshold
python tools/gap_miner.py --apply --cell thermal --min-evidence 2.0

# Custom output directory (overrides the pin-derived default)
python tools/gap_miner.py --apply --output-dir /tmp/curiosity-run

# Machine-readable
python tools/gap_miner.py --json
```

`--apply` is required for any disk write. The default posture is dry-run, matching the Stage-2 indexer convention. The default `--output-dir` is derived from `pin_hash` so two reproductions of the same input land in the same directory without operator intervention.

## 9. Sample real-data run

Run against the live campaign at session start (pin captured 2026-04-25):

```
rows_scanned:    35 445
rows_unresolved: 7 077
rows_high_lat:   8 760
curiosity items: 80 (cap 80)
gap_types:       low_confidence_routing 73, missing_solver 7
top cell hits:   safety, seasonal, system, thermal
unattributed:    188 / 200 raw clusters
```

The high "unattributed + low_confidence_routing" share is the expected signal under our standing finding: the UI-gauntlet campaign ran weather/news/adversarial query distributions, not solver-domain distributions. The miner correctly classifies most of that traffic as `clarify_routing` rather than `propose_solver`. A small thermal / energy / safety cluster nucleus is real and ranked at the top — that's the first concrete teacher target after the campaign closes.

## 10. Where this fits in Phase 8.5 longer arc

| Session | Deliverable | Status |
|---|---|---|
| A (this) | Gap miner core, curiosity contract, fixtures, deterministic tests, vision doc | landed |
| B | Embedding-based clustering, dream curriculum read of teacher packs, contradiction-surface scanner | pending |
| C | Learned ranker, schema v2, runtime repoint coordination | pending |
| R7.5 | Chaos testing of the writer side under adversarial event-log replay | pending |

Each later session inherits this commit's pin contract, JSONL line schema, and per-cell pack format unchanged. If something has to break, it lands in a new schema version, not a silent semantic shift.
