# EVOLUTION_INDEX

Per-round meta-metric index for WaggleDance autonomy work.
This is the **Axis C** (cumulative learning velocity) substrate from R20.

Schema agreed in `iterations/codex_scout_tasks/r20_synthesis_2026_05_09.md`
(R20 synthesis, 2026-05-09). Every R20.x PR (and every later round PR)
must add one entry to the `entries:` list below. Entries are append-only
in order of merge time. The validator at
`tools/check_evolution_index.py` enforces schema fields and types.

## Schema

```yaml
- session_id: <string>            # short stable identifier, e.g. r17-cand1
  pr: <int|null>                  # PR number on github; null for non-PR rounds
  owner: <agent>                  # claude | codex | operator
  reviewer: <agent|null>          # the other agent; null if no review yet
  merged_utc: <ISO8601|null>      # null if not yet merged
  # Axis A — per-operation latency
  axis_a_before_ms: <float|null>
  axis_a_after_ms: <float|null>
  axis_a_metric: <string|null>    # operation name, e.g. TrustAdapter.get_ranking_512
  axis_a_snapshot: <string|null>  # snapshot hash used for the bench
  # Axis B — per-decision quality
  axis_b_quality: <float|null>    # null until a quality oracle exists
  # Axis C — cumulative learning velocity
  axis_c_claim_to_push_minutes: <int|null>
  axis_c_push_to_merge_minutes: <int|null>
  # Other discipline columns
  runtime_behavior_changed: <bool>
  pre_merge_findings_caught: <int>
  post_merge_audit_findings: <int>
  failed_attempts: <int>           # abandoned candidates that fed into this PR
  lessons_learned: <string>
  next_bottleneck: <string>
```

## Conventions

- `axis_a_before_ms` / `axis_a_after_ms` use the **same machine + same snapshot**
  whenever possible. When that is impossible (e.g., reusing a Codex scout's
  baseline from a different machine), record both numbers and label the
  apples-to-apples pair in `lessons_learned`.
- `axis_b_quality` stays `null` until R20.3 (or later) introduces a quality
  oracle for at least one decision. Even the deployment-gate threshold
  (≥20% improvement) is recorded here when it applies.
- `runtime_behavior_changed=false` for scout reports, abandon decisions,
  hash-fix follow-ups, and pure-docs entries. The runtime/build-time
  distinction matters for Profile S regression checks (R20.4).
- `pre_merge_findings_caught` is the count of `finding/open` and
  `decision/blocked` bridge events on this PR's review path. Reviewer
  audits that catch a real issue land here. Bridge timestamp is the
  source of truth.

## Entries

```yaml
entries:

- session_id: r17-cand1-trust-adapter-caching
  pr: 165
  owner: codex
  reviewer: claude
  merged_utc: 2026-05-09T16:42:40Z
  axis_a_before_ms: 22.97
  axis_a_after_ms: 0.86
  axis_a_metric: TrustAdapter.get_ranking_512
  axis_a_snapshot: bb3e93036f3e
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 9
  axis_c_push_to_merge_minutes: 10
  runtime_behavior_changed: true
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    Cache running aggregate per target with trim-rebuild. The OLD per-target
    loop was mathematically equivalent to the NEW running cache because the
    decay's global (now - t_max) factor cancels in the score ratio.
  next_bottleneck: vector_events.read_events full scan at 10k events

- session_id: r17-cand2-vector-events-offset-reader
  pr: 166
  owner: claude
  reviewer: codex
  merged_utc: 2026-05-09T17:23:04Z
  axis_a_before_ms: 108.84
  axis_a_after_ms: 1.60
  axis_a_metric: vector_events.read_events_from_offset_incr_100_after_full_10k
  axis_a_snapshot: bb3e93036f3e
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 4
  axis_c_push_to_merge_minutes: 22
  runtime_behavior_changed: true
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    Byte-offset checkpoint reader gives O(new_events) replay. Binary-mode
    read keeps offsets exact under platform newline rewriting. Trailing
    partial line is NOT consumed so writer mid-flush is safe.
  next_bottleneck: EventLogAdapter list buffer tail-copy churn

- session_id: r17-cand3-event-log-deque
  pr: 167
  owner: claude
  reviewer: codex
  merged_utc: 2026-05-09T17:48:37Z
  axis_a_before_ms: 81.41
  axis_a_after_ms: 25.49
  axis_a_metric: EventLogAdapter.log_event_bulk_5000
  axis_a_snapshot: bb3e93036f3e
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 3
  axis_c_push_to_merge_minutes: 21
  runtime_behavior_changed: true
  pre_merge_findings_caught: 1
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    deque(maxlen) for O(1) eviction. Codex's review caught a concurrency
    bug: count_by_type / get_quality_distribution were assigning
    self._buffer inside the lock but iterating after release; with deque
    that races into RuntimeError: deque mutated during iteration. Fixed
    by snapshotting list(self._buffer) under the lock, matching stats() /
    query() pattern.
  next_bottleneck: hexagon select_origin_cell + deliver_batch + neighbor lookups

- session_id: r18-hex-latency-scout
  pr: 168
  owner: codex
  reviewer: claude
  merged_utc: 2026-05-09T18:08:50Z
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: null
  axis_a_snapshot: 2a03ff973bf1
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 7
  axis_c_push_to_merge_minutes: 8
  runtime_behavior_changed: false
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    Scout-only PR. Three hex hot-paths flagged: get_neighbor_cells (253ms/20k),
    deliver_batch ring_request (95.59ms/20k), select_origin_cell (65.19ms/2k).
  next_bottleneck: implement Cand 1 (hex neighbor cache)

- session_id: r18-hash-fix-canonical
  pr: 169
  owner: codex
  reviewer: claude
  merged_utc: 2026-05-09T18:54:36Z
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: null
  axis_a_snapshot: df1e5b1e3a5e
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 7
  axis_c_push_to_merge_minutes: 43
  runtime_behavior_changed: false
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    Snapshot hashes must be line-ending stable across Git's CRLF rewriting
    on Windows. Hash canonical JSON content, not raw file bytes. Records
    BOTH old (2a03ff973bf1) and new (df1e5b1e3a5e) hashes for backward
    reference.
  next_bottleneck: continue Cand 1

- session_id: r18-cand1-hex-neighbor-cache
  pr: 170
  owner: claude
  reviewer: codex
  merged_utc: 2026-05-09T18:25:57Z
  axis_a_before_ms: 199.29
  axis_a_after_ms: 21.78
  axis_a_metric: HexTopologyRegistry.get_neighbor_cells_repeated_20k
  axis_a_snapshot: 72d580beb304
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 4
  axis_c_push_to_merge_minutes: 11
  runtime_behavior_changed: true
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    Cache ring-1 neighbor cell IDs at load time; topology is immutable
    post-load. Cache stores IDs only — enabled-state lookup at query
    time so disable-after-load still filters. Codex review claim
    auto-released stale (heartbeat 482s old); Claude autonomous-merged
    per CLAUDE.md rule 9 + operator resilience.
  next_bottleneck: select_origin_cell selector index

- session_id: r18-cand2-deliver-batch-relation-index
  pr: null
  owner: claude
  reviewer: null
  merged_utc: null
  axis_a_before_ms: 44.46
  axis_a_after_ms: 52.65
  axis_a_metric: ring_messaging.deliver_batch_ring_request_20k
  axis_a_snapshot: 72d580beb304
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 8
  axis_c_push_to_merge_minutes: null
  runtime_behavior_changed: false
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 1
  lessons_learned: |
    Architecturally-correct O(1) frozenset membership replacement of
    O(degree) sorted-list-in. AFTER medians 18-34% SLOWER at the 20k
    single-batch workload — relation-index build cost matched per-message
    savings. Parity test passed (behavior preserved). No production
    callers of deliver_batch (only microbench + tests). Abandoned with
    decision document at iterations/codex_scout_tasks/r18c2_abandoned_2026_05_09.md.
  next_bottleneck: select_origin_cell (Cand 3) — clearer measurement workload

- session_id: r18-cand3-selector-index
  pr: 171
  owner: claude
  reviewer: codex
  merged_utc: 2026-05-09T18:49:23Z
  axis_a_before_ms: 41.43
  axis_a_after_ms: 21.33
  axis_a_metric: HexTopologyRegistry.select_origin_cell_repeated_2k
  axis_a_snapshot: 72d580beb304
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 4
  axis_c_push_to_merge_minutes: 11
  runtime_behavior_changed: true
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    Pre-lowercase domain/tag selectors at load time; substring matching
    requires string semantics so a token-inverted index doesn't apply.
    Worst-case AFTER (22.35 ms) beat best-case BEFORE (32.86 ms) so the
    signal was clean despite microbench noise on this hardware. Codex
    silent — Claude autonomous-merged.
  next_bottleneck: 10k+ solver scaling concrete blockers

- session_id: r19-priority3-scout-and-cand1
  pr: 172
  owner: claude
  reviewer: codex
  merged_utc: 2026-05-09T19:14:20Z
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: tools.run_solver_scale_proof.bulk_load_descriptors_redundant_select
  axis_a_snapshot: phase17a_synth_v1
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 5
  axis_c_push_to_merge_minutes: 10
  runtime_behavior_changed: false
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    Removing redundant get_solver SELECT after upsert_solver is correct
    code hygiene but bench at 1000 descriptors is noise-dominated; the
    10000-fewer-SELECTs at full scale is the measurable shape but needs
    a 2.5-min full 10k bench to confirm. Cand 2 (transaction batching)
    deferred — sized at 5-20x build speedup at 10k. Cand 3 (lookup p99
    14.10 ms at 10k) deferred to R21.
  next_bottleneck: build-phase transaction batching (R19 Cand 2) OR R20 sprint

- session_id: r20-routing-and-claude-baseline
  pr: 173
  owner: claude
  reviewer: null
  merged_utc: 2026-05-09T19:29:30Z
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: null
  axis_a_snapshot: null
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 4
  axis_c_push_to_merge_minutes: 10
  runtime_behavior_changed: false
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    R20 ROUTING fired with sha256-pinned prompt copy and decision/proposed
    bridge event. Claude wrote Part 0-5 baseline solo because Codex was
    silent. Implementation is gated on synthesis file existing on main.
  next_bottleneck: Codex stand-in baseline + synthesis (so R20.1 can begin)

- session_id: r20-codex-standin-and-synthesis
  pr: 174
  owner: claude
  reviewer: null
  merged_utc: 2026-05-09T19:42:25Z
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: null
  axis_a_snapshot: null
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 12
  axis_c_push_to_merge_minutes: 10
  runtime_behavior_changed: false
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    Resilience-driven solo synthesis with Codex stand-in clearly marked
    authored-by claude on behalf of absent codex. Synthesis reserves a
    Codex amendment block at file bottom so when Codex re-attaches they
    can append without rewriting. PR plan ratified: R20.1 -> R20.5 ->
    R20.2 -> R20.4 -> R20.3 -> R20.6.
  next_bottleneck: R20.1 EVOLUTION_INDEX backfill (this round)

```

## Cumulative axis-A summary (as of R20.1)

Of the entries with both before and after numbers (six measurable PRs):

| PR | Operation | Before | After | Speedup |
|---|---|---:|---:|---:|
| #165 | TrustAdapter.get_ranking 512 | 22.97 ms | 0.86 ms | ~26.7× |
| #166 | vector_events incr 100 vs full 10k | 108.84 ms | 1.60 ms | ~68.0× |
| #167 | EventLogAdapter.log_event 5000 | 81.41 ms | 25.49 ms | ~3.2× |
| #170 | get_neighbor_cells 20k | 199.29 ms | 21.78 ms | ~9.1× |
| (—)  | deliver_batch 20k (abandoned) | 44.46 ms | 52.65 ms | 0.84× |
| #171 | select_origin_cell 2000 | 41.43 ms | 21.33 ms | ~1.94× |

Total measurable improvement landed: 5 of 6 attempts, 1 abandoned with
decision document.

## R20 round status (live)

- R20.1 (this PR — Claude) — backfill in progress
- R20.5 (Claude, next per synthesis order) — pending
- R20.2 (Codex) — pending Codex re-attach OR Claude takeover
- R20.4 (Claude) — pending
- R20.3 (Codex) — pending
- R20.6 (Codex) — gated on R20.1–R20.5 status
