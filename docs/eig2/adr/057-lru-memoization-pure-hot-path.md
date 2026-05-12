# ADR-057 — LRU memoization on pure hot-path functions

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: L34 hot-path perf budgets (PR #290), L35 regex pattern cache (PR #291)

## Context

The 50-leaps menu (L40) calls for **memoization with bounded LRU on pure hot-path functions**. Pure-function hot-path callees today recompute each invocation; memoization could push warm-state common queries to sub-microsecond.

Audit confirms only `waggledance/adapters/http/routes/status.py:16` uses `@lru_cache` today. Other candidates (per Claude's per-adapter scan):
- `solver_router._has_signal` — already memoized via `_SIGNAL_PATTERN_CACHE` (L35 pin)
- `aliasing.AliasRegistry.resolve` — could benefit
- Domain-derivation helpers in container.py

## Decision

A registry of approved `@lru_cache`-decorated functions lives at `docs/eig2/contracts/lru_memoization.json`:

```yaml
allowed_lru_sites:
  - module: waggledance/adapters/http/routes/status.py
    function: get_readiness_state
    maxsize: 1
  - module: waggledance/core/capabilities/aliasing.py
    function: AliasRegistry.resolve
    maxsize: 1024
  ...
```

Adding a new `@lru_cache` site requires:
1. Function MUST be pure (no side effects, deterministic given args).
2. All args MUST be hashable.
3. Function MUST be on a verified hot path (per L34 perf budget).
4. Maxsize MUST be bounded (no `maxsize=None`).

A contract test scans the codebase for `@lru_cache` usages and asserts ALL of them are in the allowlist.

## Invariants (LRU-001..LRU-007)

1. **Allowlist source of truth**: every `@lru_cache` in waggledance/ MUST be in the YAML allowlist.
2. **Maxsize bounded**: no `@lru_cache(maxsize=None)` permitted.
3. **Hashable args required**: function args MUST all be hashable types.
4. **Pure-function rule**: function MUST be deterministic + side-effect-free.
5. **Hot-path justification**: every allowlist entry has a `justification` field citing the hot-path measurement.
6. **Reviewable**: adding to allowlist requires PR review + perf benchmark before/after.
7. **No `@cache` (unbounded)**: only `@lru_cache(maxsize=K)`.

Contract: `docs/eig2/contracts/lru_memoization.json`. Tests: `tests/contracts/test_lru_memoization.py`.
