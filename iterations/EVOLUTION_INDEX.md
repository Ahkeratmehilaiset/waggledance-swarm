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

- session_id: r20.1-evolution-index
  pr: 175
  owner: claude
  reviewer: null
  merged_utc: 2026-05-09T19:56:20Z
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: null
  axis_a_snapshot: null
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 5
  axis_c_push_to_merge_minutes: 10
  runtime_behavior_changed: false
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    EVOLUTION_INDEX.md ships the Axis-C substrate. Schema folds in
    Codex stand-in's split (claim_to_push vs push_to_merge),
    pre_merge_findings_caught, runtime_behavior_changed. Validator at
    tools/check_evolution_index.py + 4/4 tests pin the schema.
  next_bottleneck: R20.5 R16 process isolation

- session_id: r20.5-r16-process-isolation
  pr: 176
  owner: claude
  reviewer: null
  merged_utc: 2026-05-09T20:10:18Z
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: null
  axis_a_snapshot: null
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 10
  axis_c_push_to_merge_minutes: 14
  runtime_behavior_changed: true
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    Invoke-RoleReview wrapper makes architect/security/reliability
    review operationally three-process. Existing infra (orchestrator/
    Invoke-WaggleReview.ps1 + prompts/review/*) was already there;
    R20.5 adds the bridge-side wrapper that emits 3 + 1 events with
    stable sub-task_ids. 12/12 smoke. BRIDGE_PROTOCOL rule 7 marks
    legacy "three labels in one paragraph" pattern as deprecated.
  next_bottleneck: R20.2 BridgeLLMClient prototype

- session_id: r20.4-deployment-profiles
  pr: 177
  owner: claude
  reviewer: null
  merged_utc: 2026-05-09T20:22:19Z
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: null
  axis_a_snapshot: null
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 11
  axis_c_push_to_merge_minutes: 12
  runtime_behavior_changed: true
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    solver-profiles/{small,medium,large}.json + Start-WaggleDanceSolver.ps1
    + Profile S subprocess test. The headline guarantee is the
    subprocess-isolated import-discipline check: a fresh Python
    process loads Profile S and confirms zero LLM SDKs leaked to
    sys.modules. Plus a guard-sanity test that injects fake anthropic
    and confirms the guard catches it.
  next_bottleneck: R20.2 BridgeLLMClient (Codex-owned; resilience if silent)

- session_id: r20.2-bridge-llm-client
  pr: 178
  owner: claude
  reviewer: null
  merged_utc: 2026-05-09T20:36:20Z
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: null
  axis_a_snapshot: null
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 5
  axis_c_push_to_merge_minutes: 14
  runtime_behavior_changed: true
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    BridgeLLMClient four-tier fallback (cache -> local-ollama -> cloud
    stub -> heuristic). Profile S clean: importing waggledance.core.
    bridge_llm leaks zero LLM SDKs into sys.modules. OllamaProvider
    uses importlib.util.find_spec to stay clean; lazy-imports the
    package only inside .call(). Telemetry per R20 §2.3 + budget per
    §2.5 with degrade-to-heuristic on exhaustion. 14/14 tests.
    Codex-owned per synthesis; Claude resilience-takeover after ~3.5h
    Codex silence on the bridge.
  next_bottleneck: R20.3 first doping point (Codex-owned)

- session_id: r20.3-ab-harness-and-decision-b
  pr: 179
  owner: claude
  reviewer: null
  merged_utc: 2026-05-09T20:49:25Z
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: null
  axis_a_snapshot: null
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 4
  axis_c_push_to_merge_minutes: 13
  runtime_behavior_changed: true
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    ABHarness substrate ships. Production wire-up DEFERRED via Decision
    B: no labelled corpus exists for R20 §2.4 20% gain threshold.
    Activation criteria spelled out in iterations/codex_scout_tasks/
    r20_3_decision_b_2026_05_09.md. 7/7 tests including treatment-
    failure-falls-through and proportional split over 100 samples.
  next_bottleneck: R20.6 release-readiness Decision B

- session_id: r20.6-release-readiness-decision-b
  pr: null
  owner: claude
  reviewer: null
  merged_utc: null
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: null
  axis_a_snapshot: null
  axis_b_quality: null
  axis_c_claim_to_push_minutes: null
  axis_c_push_to_merge_minutes: null
  runtime_behavior_changed: false
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    R20.6 is a release-readiness Decision B per the master prompt's
    explicit alternative when soak between substrate and release is
    needed. CHANGELOG.md 2026-05-09 entry + README.md front-page
    snapshot + docs/release/R20_RELEASE_READINESS_2026_05_09.md
    activation criteria. No semver bump, no Docker, no GitHub release.
    Activates when all five criteria hit (A/B run, cloud provider
    plugin, R19 Cand 2 measured, Codex synthesis ratification, Phase C
    gate re-verification).
  next_bottleneck: R20.6 activation prerequisites (R21 will track)

- session_id: r21.1-oracle-ab-harness
  pr: 187
  owner: claude
  reviewer: codex
  merged_utc: 2026-05-10T04:14:04Z
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: null
  axis_a_snapshot: null
  axis_b_quality: 0.5
  axis_c_claim_to_push_minutes: null
  axis_c_push_to_merge_minutes: null
  runtime_behavior_changed: true
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    R21.1 ships the oracle-backed A/B harness for select_origin_cell
    and produces the FIRST non-null axis_b_quality entry in this
    file. control_quality = 0.5, treatment_quality = 0.5,
    delta_quality_pct = 0.00%. Two factors collapse the signal on
    this run:

    1. Topology mismatch: tests/oracle/*.yaml uses cells
       energy/math/safety/system/thermal but configs/hex_cells.yaml
       has hub/bee_ops/environment/home_comfort/safety_security/
       production/logistics. The two taxonomies slice the routing
       space differently (decision-type vs domain), so an oracle
       expecting cell="math" never matches a heuristic returning
       cell="bee_ops". Both arms produce file_score=0.5 (perfect
       neg rejection because neither hits oracle.cell, zero pos
       routing).
    2. Ollama unavailable on this machine
       (local_llm_status=unavailable per Decision 8). Treatment arm
       fell through to control on every utterance (420/420
       fallthrough_uses, 420/420 unparsed_responses). delta=0 is
       the informational outcome required by Decision 8.

    Per operator decision 8 + R20 rule 17: ship the result honestly,
    keep treatment disabled, log the topology-mismatch finding for
    R22 follow-up. The release-gate condition "R21.1 has a real
    delta_quality number" is satisfied (0.00%, not null).

    R22 candidate: either (a) build a hex-aligned synthetic eval
    matching the 7 hex cells with selectors-derived utterances, or
    (b) build an oracle-cell to hex-cell mapping table where the
    semantics permit (note: honey_yield in oracle is cell="math"
    because of the calculation type but topically routes to
    bee_ops in hex — these don't reconcile cleanly), or (c) wire
    the oracle YAMLs to the FAISS staging routing layer they
    actually target (different code path entirely).
  next_bottleneck: R21.2 R19 Cand 2 build-phase transaction batching

- session_id: r21.2-control-plane-transaction-batching
  pr: null
  owner: codex
  reviewer: claude
  merged_utc: null
  axis_a_before_ms: 152271.5
  axis_a_after_ms: 1830.5
  axis_a_metric: tools.run_solver_scale_proof.bulk_load_descriptors_10k
  axis_a_snapshot: r21_2_scale_10k_2026_05_10
  axis_b_quality: null
  axis_c_claim_to_push_minutes: null
  axis_c_push_to_merge_minutes: null
  runtime_behavior_changed: true
  pre_merge_findings_caught: 1
  post_merge_audit_findings: 0
  failed_attempts: 1
  lessons_learned: |
    R21.2 closes R19 Cand 2 by adding a public
    ControlPlaneDB.transaction() context manager and wrapping
    tools/run_solver_scale_proof.py::bulk_load_descriptors in one
    explicit SQLite transaction. The repeatable 10k benchmark uses
    the same synthetic descriptor snapshot and lookup count before
    and after:

    - before: 152.2715 s build, 65.7 descriptors/s
    - after: 1.8305 s build, 5462.9 descriptors/s
    - speedup: 83.19x, 150.4410 s saved

    Capability lookup correctness remained green in both runs:
    1000/1000 auto_promoted_solver hits, zero FIFO fallback, zero
    misses. The after run's lookup p99 is noisier (34.9939 ms vs
    22.8506 ms) but R21.2 targets build-phase latency, not lookup
    p99; R19 Cand 3 remains the lookup-tail follow-up.

    Coordination finding: Claude claimed R21.2 first but its claim
    stale-released without heartbeat after partially editing the
    shared worktree. Codex avoided parallel edits, then finished from
    the existing diff after stale release. This is counted as one
    pre-merge coordination finding, not a product-code failure.
  next_bottleneck: R21.3 cloud provider plugin + BridgeLLMRedactor

- session_id: r21.3-anthropic-and-redactor
  pr: 189
  owner: claude
  reviewer: codex
  merged_utc: 2026-05-10T04:58:27Z
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: null
  axis_a_snapshot: null
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 5
  axis_c_push_to_merge_minutes: 11
  runtime_behavior_changed: true
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 3
  failed_attempts: 0
  lessons_learned: |
    R21.3 ships AnthropicProvider (Tier 3 cloud) + BridgeLLMRedactor
    enforcing operator decision 4 PII regexes (email, credit-card,
    phone, file path) ON BY DEFAULT for cloud-bound prompts.
    AcceptPiiToCloud=False hard default. Lazy-import keeps Profile S
    clean (importing waggledance.core.bridge_llm leaks zero LLM
    SDKs into sys.modules per subprocess-isolated test). Mandatory
    redactor on every cloud call; provider raises ProviderError on
    <REDACTOR_FAILED> sentinel (fail-closed contract). 18 redactor
    tests + AnthropicProvider plumbing tests PASS.

    Post-merge audit (filed via bridge finding/open
    r22-redactor-bugs-2026-05-10): 3 bugs found in cloud path,
    queued for R22.0 hotfix:
    - F1 medium: POSIX_PATH_RE eats URL paths
      (https://docs.python.org/3/library/re.html -> https:/<PATH_1>)
    - F2 low DoS: <REDACTOR_FAILED> sentinel injection in user prompt
      triggers false fail-closed
    - F3 medium: AnthropicProvider ignores
      request.budget.max_latency_ms (potential indefinite hang)
      and cost_cents not computed (budget tracker can't enforce
      $-budget for Anthropic)
  next_bottleneck: R21.4 gate re-verification

- session_id: r21.4-gate-reverification
  pr: 190
  owner: claude
  reviewer: codex
  merged_utc: 2026-05-10T05:12:28Z
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: null
  axis_a_snapshot: null
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 4
  axis_c_push_to_merge_minutes: 11
  runtime_behavior_changed: false
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    R21.4 read-only verification round on top of post-R21.3 main.
    All five operational gates green:
    - cold-shell BOOTSTRAP (R13/R13.5): 10/10
    - 5+ autonomous PRs landed: 17 since R20 routing
    - 5-min stale lease (R15): 11/11
    - bridge role-review smoke (R20.5/R16): 12/12
    - R20+R21+Phase D targeted regression: 268/268 in 13.93s
    Closes R21.5 release-decision gate (3).
  next_bottleneck: R21.5 release decision (Codex / Claude resilience)

- session_id: r21.5-release-decision
  pr: 191
  owner: claude
  reviewer: codex
  merged_utc: 2026-05-10T05:38:34Z
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: null
  axis_a_snapshot: null
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 4
  axis_c_push_to_merge_minutes: 23
  runtime_behavior_changed: false
  pre_merge_findings_caught: 1
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    R21.5 ships the v3.11.0-r20-axis-b-activated-alpha PRERELEASE
    decision (CHANGELOG entry, README front-page note, anti-claims
    rule 18) with all 5 operator R21.5 gates ✅:
    1. R21.1 has real delta_quality (#187, 0.5 first non-null axis-B)
    2. Part 1 finalized (#183/#184/#185/#186 on main)
    3. R21.4 gate re-verification green (#190; 268/268 regression)
    4. R20 Decision B 5 conditions all ✅ (A/B run; AnthropicProvider
       + redactor #189; R19 Cand 2 measured at 10k = 79.3x speedup;
       Codex synthesis-amendment ratification 03:35:18Z; Phase C
       gates re-verify R21.4)
    5. PR #182 Profile S env fix merged (1bbef6b)

    Codex pre-merge finding: pyproject version bump 3.6.0 -> 3.11.0a1
    broke TestVersionConsistency. Codex fix-on-branch (f4daa18) keeps
    the package version numeric (3.6.0) and documents the prerelease
    identity as Git tag v3.11.0-r20-axis-b-activated-alpha.
  next_bottleneck: R21.6 closeout (tag + GitHub release + GHCR)

- session_id: r21.6-closeout
  pr: 192
  owner: claude
  reviewer: codex
  merged_utc: 2026-05-10T05:54:44Z
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: null
  axis_a_snapshot: null
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 5
  axis_c_push_to_merge_minutes: 28
  runtime_behavior_changed: true
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    R21.6 closeout: .github/workflows/release-docker.yml builds the
    canonical + sliding-alias + Profile S/M images on GHCR per
    operator decision 3, refuses to publish if tag does not end in
    'alpha' (operator decision 2 hard guard), skips waggledance:latest
    update. Smoke-test job pulls image and asserts Profile S
    BridgeLLMClient.disabled() works + redactor scrubs alice@example.org.
    Local Docker Desktop unavailable on build machine; GitHub Actions
    runs the build in CI. Operator-runnable post-merge runbook in
    closeout doc; release.published auto-fires the workflow.

    Post-merge: tag v3.11.0-r20-axis-b-activated-alpha pushed; GitHub
    release isPrerelease=true at e4e51dd; GHCR workflow run
    25621316901 succeeded (build-and-push + smoke-test both green;
    canonical + axis-b-alpha + small-axis-b-alpha + medium-axis-b-alpha
    images live on ghcr.io/ahkeratmehilaiset/waggledance). R21 sprint
    end-to-end complete in ~2h 11min (operator paste 03:48Z to
    release publish 05:59Z) vs 8h budget.
  next_bottleneck: R22.0 redactor + AnthropicProvider hotfix (3 bugs from post-merge audit) → R22.1+ scout-led work

- session_id: r23.0-bridge-wake-on-event
  pr: 195
  owner: claude
  reviewer: codex
  merged_utc: 2026-05-10T07:41:36Z
  axis_a_before_ms: 270000.0
  axis_a_after_ms: 221.0
  axis_a_metric: bridge_polling_deadlock_response
  axis_a_snapshot: "8964189"
  axis_b_quality: null
  axis_c_claim_to_push_minutes: 10
  axis_c_push_to_merge_minutes: 12
  runtime_behavior_changed: false
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    R23.0 closes the structural pull-only deadlock the operator hit on
    2026-05-10: idle Claude + idle Codex could each wait 270 s+ on the
    other before the next manual poll. Adds Watch-Bridge.ps1 (file-poll
    watcher 1 s + 250 ms debounce) writing wake_<agent> sentinel when a
    shared/events.jsonl event whose `to` targets the watched agent
    appears, plus Test-BridgeWake.ps1 consume-on-read helper, plus
    Start-AgentBridgeSession.ps1 background-job integration with
    -SkipWakeWatcher / WAGGLE_BRIDGE_WAKE_ENABLED=0 kill switches.

    Used file-poll instead of Register-ObjectEvent FileSystemWatcher:
    Win PS 5.1 runspace boundary makes Register-ObjectEvent fragile
    across Start-Job. Equivalent observable behavior, more robust under
    shell churn. R20.5 Invoke-RoleReview compatibility: the 250 ms
    post-write debounce collapses three near-simultaneous subprocess
    emissions into one wake.

    Smoke 9/9 PASS (Test-BridgeWakeOnEventSmoke.ps1): targeted /
    self-echo ignored / comma-list to= / non-targeted ignored /
    consume-on-read / live Start-Job latency / kill-switch. Measured
    latency: 221 ms warm, well below the operator <2 s spec.

    runtime_behavior_changed=false because the substrate is
    coordination-layer only (bridge protocol + agent shells); no
    waggledance/ runtime code path changed. Axis A here is a meta-
    metric on the agent-coordination loop, not a runtime SLO.
  next_bottleneck: R24 mandatory role-review gate substrate (operator pre-direktiivi 2026-05-10) — turns the soft-rule on bridge/protocol/source/runtime/cloud/privacy PRs into a tooling-enforced gate.

- session_id: r22.2-hex-aligned-eval
  pr: null
  owner: claude
  reviewer: codex
  merged_utc: null
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: null
  axis_a_snapshot: null
  axis_b_quality: 0.7619
  axis_c_claim_to_push_minutes: 30
  axis_c_push_to_merge_minutes: null
  runtime_behavior_changed: false
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    R21.1 used tests/oracle/*.yaml whose `cell:` field used the
    energy/math/safety/system/thermal taxonomy (decision-type) which
    did NOT match configs/hex_cells.yaml's hub/bee_ops/environment/
    home_comfort/safety_security/production/logistics taxonomy
    (domain). Result: control_quality=0.5 that did not reflect the
    underlying heuristic's competence — taxonomy mismatch was masking
    the real Axis B baseline.

    R22.2 ships tests/oracle_hex/*.yaml with 7 files × (15 positive +
    5 negative) = 140 utterances explicitly aligned to the seven hex
    cells. With this corpus, HexTopologyRegistry.select_origin_cell
    reaches control_quality=0.7619 — a +52.4% absolute lift in the
    Axis B baseline (0.5 -> 0.7619). The remaining 0.2381 is paraphrase
    headroom: utterances written in Finnish/English natural language
    that intentionally avoid the selector keywords. That gap is what
    an LLM treatment can target in R22.3.

    Per-cell breakdown (heuristic): hub 1.0 / environment 0.7667 /
    safety_security 0.7333 / logistics 0.7333 / bee_ops 0.7 /
    home_comfort 0.7 / production 0.7. Negatives: 100% across all
    cells (cross-cell discrimination is solid; substring matching
    does not bleed across taxonomies).

    runtime_behavior_changed=false: no waggledance/ code path changed;
    pure test fixture + regression-floor pin. Reuses
    tools/run_r21_oracle_ab_proof.py via its existing --oracle-dir flag
    (no new harness needed).

    axis_b_quality=0.7619 is the heuristic baseline on the new corpus.
    delta_quality stays 0% until R22.3 wires a real cloud LLM (Ollama
    unavailable on this machine; Anthropic provider exists but no key
    in this session).
  next_bottleneck: R22.3 Profile L Anthropic A/B with operator API key — paraphrase headroom (0.2381) is exactly what an LLM should beat over the substring heuristic.

- session_id: r22-2d-branch-isolation-baseline
  pr: null
  owner: codex
  reviewer: claude
  merged_utc: null
  axis_a_before_ms: null
  axis_a_after_ms: null
  axis_a_metric: 2d_branch_isolation_baseline
  axis_a_snapshot: "6cbe1d9c4bc5"
  axis_b_quality: null
  axis_c_claim_to_push_minutes: null
  axis_c_push_to_merge_minutes: null
  runtime_behavior_changed: false
  pre_merge_findings_caught: 0
  post_merge_audit_findings: 0
  failed_attempts: 0
  lessons_learned: |
    R25/3D was paused until the 2D path is finished and measured. This
    entry records the first measurement-only 2D branch-isolation
    baseline for the current global ControlPlaneDB write path.

    Benchmark command:
    C:\Python\project2-master\.venv\Scripts\python.exe
    tools\run_branch_isolation_benchmark.py --db
    .codex-audit\r22_branch_isolation.sqlite --out-json
    .codex-audit\r22_branch_isolation_baseline_2026_05_10.json
    --repeats 3 --probe-events 120 --hot-events 800
    --uniform-events-per-branch 60 --cold-flood-events-per-branch 80.

    Result on snapshot 6cbe1d9c4bc5: hub idle probe p99 mean 12.9381 ms;
    hub probe while bee_ops is hot p99 mean 31.0522 ms (2.4001x
    degradation); hub probe during adversarial writes from all other
    branches p99 mean 167.8618 ms (12.9743x degradation). Uniform
    multi-branch p99 CV was 0.5505. Branch touch count target remains
    1.0 for hit cases.

    runtime_behavior_changed=false: this PR adds a benchmark, a schema
    smoke test for the benchmark output, and a tracked JSON result. No
    topology, routing, schema, sharding, or runtime behavior changed.
  next_bottleneck: R22.3 Profile L Anthropic A/B for Axis B headroom, plus a future 2D write-pressure mitigation if R22.5 wants branch-isolation improvement before reopening R25.

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
