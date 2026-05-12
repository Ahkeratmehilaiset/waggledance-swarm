# ADR-032 — Cross-agent failed-candidate broadcast

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: ADR-021 (progressive replay), ADR-024 (compact decision card), ADR-031 (confidence-bin gap mining)

## Context

Today, when one agent proposes a solver candidate and it fails canary evaluation, the failure is recorded locally to that agent's autogrowth state. Other agents in the swarm discover similar candidates independently and re-fail them independently. The system spends N× the canary budget rediscovering the same negative result.

The 50-leaps menu (L22) proposes **cross-agent failed-candidate broadcast**: when one agent's candidate fails canary, broadcast a structured failure pattern to other agents so they avoid the same proposal class. Shared anti-knowledge.

## Decision

A new `failed_candidate_broadcast_event` is appended to the bridge events JSONL when canary evaluation rejects a candidate:

```json
{
  "event_type": "failed_candidate_broadcast",
  "candidate_class_hash": "<sha256 of normalized candidate structure>",
  "rejection_reason": "<short reason code>",
  "rejected_at_utc": "<ISO8601>",
  "rejecting_agent_id": "<agent_id>",
  "feature_fingerprint": "<sha256 of feature vector>",
  "ttl_hours": 720
}
```

Subscribers (other agents' gap-miners) consume the event stream and cache the `candidate_class_hash` in a bloom filter or equivalent. Before proposing a new candidate, the gap-miner checks its local anti-knowledge cache; matches are pre-skipped.

TTL=720 hours (30 days) bounds the anti-knowledge window. Stale failures may be due to outdated training data; allowing rediscovery after 30 days lets the system retry.

## Consequences

### Capability growth

* Reduced wasted canary cycles → MORE budget for genuinely novel candidates.
* Cross-agent learning is fully bridge-mediated, no shared database. Each agent maintains its own anti-knowledge cache.

### Storage / bandwidth

* Each failure ≈ 200 bytes JSONL event. Bridge events.jsonl growth: ~100 KB/day at moderate canary volume. Negligible.

### Operational

* Bloom-filter false positives MAY skip a genuinely novel candidate. Acceptable trade for the saved canary cycles. The TTL provides organic retry path.

## Invariants

Pinned in `docs/eig2/contracts/cross_agent_failed_broadcast.json` and verified by `tests/contracts/test_cross_agent_failed_broadcast.py`.

1. **Event schema.** `failed_candidate_broadcast` event has fixed required fields: event_type, candidate_class_hash, rejection_reason, rejected_at_utc, rejecting_agent_id, feature_fingerprint, ttl_hours.
2. **Hash normalization.** `candidate_class_hash` is sha256 of the candidate's NORMALIZED structure (sorted keys, lowercased terms). Two implementations of normalization MUST produce identical hash for equivalent candidates.
3. **TTL bounded.** `ttl_hours` default 720 (30 days). Pinned upper bound: 8760 (1 year). Operator-tunable in range.
4. **Bridge-mediated only.** No shared database. All anti-knowledge transfer goes through bridge events.jsonl (or its successor stream).
5. **Per-agent local cache.** Each agent maintains its own anti-knowledge cache (bloom filter or set). No central anti-knowledge service.
6. **TTL eviction.** Anti-knowledge entries older than `ttl_hours` evicted lazily on miner check.
7. **Subscriber idempotency.** A subscriber consuming the same broadcast event twice MUST NOT double-count it. Event ID or hash-based dedup required.

## Out of scope (this ADR)

* Bloom filter parameters (false positive rate tuning) — implementation choice.
* Anti-knowledge backup/restore for agent restart — separate ADR.
* Cross-swarm federation (sharing anti-knowledge across deployments) — explicitly NOT planned.

## References

* ADR-024 (compact decision card, structure hash precedent)
* ADR-031 (confidence-bin gap mining, candidate-generation context)
* 50-leaps menu: L22 (this), L21 (gap mining), L25 (failure-pattern mining)
