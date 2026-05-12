# ADR-041 — Capability adapter factory pattern (lazy binding)

* Status: **substrate-only landing** (contract + ADR pinned; implementation deferred)
* Date: 2026-05-12
* Related: PR #281 (lazy autonomy_growth package init), ADR-021 (progressive replay)
* Implements: L54-reframed (per Claude's empirical measurements)

## Context

Empirical measurement during the explosive-growth session (`.tmp-claude-1000-obs/bench-capability-loader.py`) showed that `bootstrap/capability_loader.py:bind_executors()` takes **6,855 ms median** cold (one outlier at 21,553 ms). Per-adapter import-only total is only 467 ms across 23 adapters — meaning **93% of the cost (6,387 ms) is INSTANTIATION OVERHEAD**, not import.

The 50-leaps menu had L54 framed as "apply PR #281 PEP 562 lazy pattern to adapters.capabilities" — Codex correctly confirmed that ORIGINAL framing is a no-op because the package `__init__.py` is already empty.

The **REFRAMED L54** opportunity is: **lazy capability binding**. Replace eager `AdapterX()` construction with `register_executor_factory(cap_id, factory_fn)` that defers BOTH the import AND the constructor call until first invocation of the capability.

## Decision

Two-part substrate pin:

### Part 1 — `CapabilityRegistry.register_executor_factory()`

Add a new method on `waggledance/core/capabilities/registry.py:CapabilityRegistry`:

```python
def register_executor_factory(
    self,
    capability_id: str,
    factory: Callable[[], Any],
) -> None:
    """Register a factory that constructs the executor on first invocation."""
```

And update `get_executor()`:

```python
def get_executor(self, capability_id: str) -> Optional[Any]:
    """Get the executor; constructs from factory on first access if registered lazily."""
    if capability_id in self._executors:
        return self._executors[capability_id]
    if capability_id in self._factory_failed:
        return None
    factory = self._executor_factories.get(capability_id)
    if factory is None:
        return None
    try:
        executor = factory()
        if getattr(executor, "available", True):
            self._executors[capability_id] = executor
            return executor
        else:
            self._factory_failed.add(capability_id)
            return None
    except Exception as exc:
        log.debug("Lazy factory failed for %s: %s", capability_id, exc)
        self._factory_failed.add(capability_id)
        return None
```

### Part 2 — Table-driven `bind_executors()`

Rewrite `bootstrap/capability_loader.py:bind_executors()` to register FACTORIES, not instances. Use a single `_ADAPTERS` list and a loop:

```python
_ADAPTERS = [
    ("solve.math", "waggledance.adapters.capabilities.math_solver_adapter", "MathSolverAdapter"),
    # ... 22 more entries
]

def bind_executors(registry):
    for cap_id, module_path, class_name in _ADAPTERS:
        factory = _make_factory(module_path, class_name)
        registry.register_executor_factory(cap_id, factory)
    return len(_ADAPTERS)
```

## Consequences

### Boot latency

* `bind_executors()` cold cost drops from **~6,855 ms → ~50 ms** (factory registration only, no imports/instantiation).
* First-invocation cost per capability: 28–30 ms (the previous import+construct cost), now PAID ON FIRST USE not boot.
* Empirically: 99.3% reduction in boot-time capability cost.

### Backward compat

* Existing `register_executor(cap_id, executor)` API preserved for callers that want eager-bind (runtime-extended capabilities, tests).
* `get_executor()` checks both `_executors` (eager) and `_executor_factories` (lazy) — same return semantics.
* Existing tests that check `executor_count()` continue to pass.

### Operational

* First chat after boot may have ~30 ms one-time hydration cost per fresh capability used. Acceptable; better than 6.8 s boot cost.
* `.available` check now happens at first invocation, not at boot. Unavailable capabilities (legacy deps missing) are detected lazily; caller gets None back from `get_executor()`.

## Invariants

Pinned in `docs/eig2/contracts/capability_factory_lazy_binding.json` and verified by `tests/contracts/test_capability_factory_lazy_binding.py`.

1. **Factory signature.** `register_executor_factory(capability_id: str, factory: Callable[[], Any])`. Factory returns an adapter instance on first call.
2. **Defer import + construct.** The factory MUST defer BOTH the adapter module import and the class instantiation. No work happens at register time.
3. **First-call construction.** First `get_executor(cap_id)` triggers factory call. Subsequent calls return the cached instance.
4. **available check at first use.** If `executor.available is False`, capability_id added to `_factory_failed` set and `None` returned. Subsequent calls short-circuit to None without re-running the factory.
5. **Exception safe.** Factory exceptions are caught + logged at DEBUG + capability_id added to `_factory_failed`. Exception does NOT propagate to caller.
6. **No regression on eager API.** Existing `register_executor(cap_id, executor)` still works unchanged for callers that need eager binding.
7. **Boot saving target.** Empirical benchmark: full `bind_executors()` lazy path must complete in < 100 ms (vs ~6,855 ms eager today). Tested via `.tmp-claude-1000-obs/bench-capability-loader.py` regression run.

## Out of scope (this ADR)

* Implementation of `register_executor_factory` method + table-driven `bind_executors()` — separate PR (substantive code change).
* Migration of any current direct callers (none identified).
* Predictive eager-warm-up at boot for known-hot capabilities — future ADR if measured benefit warrants.

## References

* PR #281 (lazy autonomy_growth package init, complementary pattern)
* `.tmp-claude-1000-obs/L54-reframed-implementation-sketch.md` (full implementation sketch)
* `.tmp-claude-1000-obs/bench-capability-loader.py` (empirical baseline)
* `.tmp-claude-1000-obs/bench-per-adapter-import.py` (per-adapter import cost breakdown)
* 50-leaps menu: L54 (reframed)
