# Experimental Autonomy Profile — Specification

**Status:** Specification only. There is NO experimental autonomy
profile enabled in this branch. This document defines what such a
profile would have to look like if a future session ever introduces
one — so that it cannot be introduced informally.

## Purpose of an experimental profile

A profile is a *bounded* relaxation surface: it can opt in to a small
set of explicitly-listed loosenings that go beyond the default safe
posture, but it cannot relax constitution.yaml hard rules.

Concretely, the only things an experimental profile may toggle are
items already enumerated in `HIGH_RISK_VARIANTS_DEFERRED.md`. Any
other toggle is out of scope for the profile mechanism.

## Profile contract (mandatory shape)

A profile MUST be:

1. **Named** — a single string identifier (e.g.
   `profile.experimental_v1`). The default profile is
   `profile.safe_default` and is the only one in use today.
2. **Pinned** — its toggles are recorded as a frozen JSON document with
   a structural sha256[:12] id. The id is referenced from every
   subsequent decision/observation that ran under that profile.
3. **Approved** — every transition into an experimental profile MUST
   carry a `human_approval_id` and a `rationale`.
4. **Reversible** — every profile MUST declare a documented rollback
   path that returns to `profile.safe_default` byte-stable.
5. **Observed** — while an experimental profile is active, the
   cross-capsule observer (Phase 9 §Q) records pattern observations at
   the same cadence as default operation. If `recurring_oscillation`,
   `recurring_blind_spot_class`, `recurring_proposal_bottleneck`, or
   `recurring_contradiction_pattern` cross a configured threshold, the
   profile auto-rolls back to safe default.

## What a profile can NEVER do

- Disable any constitution.yaml hard rule.
- Disable `no_runtime_auto_promotion` for the full promotion ladder
  (only the documented narrow auto-canary case from
  HIGH_RISK_VARIANTS_DEFERRED.md is even discussable).
- Disable `no_foundational_mutation` on local models (Phase 9 §N).
- Disable redaction or `no_raw_data_leakage` on cross-capsule
  observations (Phase 9 §Q).
- Disable the human-gated path for promotion to runtime authority
  (Phase 9 §M).

## Recording shape

A profile activation event is an append-only record with these fields:

```
{
  "event": "profile_activation",
  "profile_id": "profile.experimental_v1",
  "profile_sha12": "<12-hex>",
  "human_approval_id": "human:<reviewer>:<utc-iso>",
  "rationale": "...",
  "rollback_target": "profile.safe_default",
  "rollback_target_sha12": "<12-hex>",
  "no_runtime_auto_promotion": true,
  "no_foundational_mutation": true,
  "no_raw_data_leakage": true
}
```

Note the three `no_*` fields are `true` even on activation. They are
NOT toggles — they are structural invariants the profile cannot turn
off.

## Why this document exists

So that the gap between "scaffold-safe today" and "experimental
profile someday" is filled with an explicit specification rather than
left as a vague intention. A future session that wants to enable any
experimental loosening MUST realize this spec first and cannot
shortcut it.
