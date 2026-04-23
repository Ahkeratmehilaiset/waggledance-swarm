# Hybrid Retrieval Activation Plan — v3.1 Amendments

**Author:** Jani Korpi + Claude Sonnet 4.6
**Date:** 2026-04-23
**Source:** GPT-5 round-3 review (`docs/plans/GPT_response3.txt`)
**Applies to:** `docs/plans/hybrid_retrieval_activation_v3_2026-04-23.md`
**Status:** Implementation-active. These tweaks must land during Phase A and B.

GPT round-3 verdict: **"Begin Phase A.1 with 7 implementation tweaks; no v4 needed."**

Score against 14 verification points: **12 PASS / 2 WEAK / 0 MISSING.**

This document lists ONLY the 7 corrections. v3 architecture remains valid; these are
implementation-level fixes to be applied during Phase A/B coding work, NOT a re-architecture.

---

## Tweak 1 — Source-side cell disagreement must BLOCK, not WARN (§1.2)

**v3 text (current):**
```python
if declared != keyword and declared != centroid:
    WARN_REQUIRES_REVIEW(axiom["model_id"], declared, keyword, centroid)
```

**v3.1 correction:**
```python
if declared != keyword and declared != centroid:
    BLOCK_BACKFILL_UNTIL_REVIEW(
        axiom_id=axiom["model_id"],
        declared=declared,
        keyword=keyword,
        centroid=centroid,
    )
```

After human review, axiom YAML must include:
```yaml
placement_review:
  status: approved
  reviewed_by: Jani | Claude | GPT | human
  reason: "Declared thermal is correct because heat_pump_cop is fundamentally a thermal-domain solver, energy is just an output unit"
  reviewed_at: 2026-04-23
```

Backfill skips axioms with `placement_review.status != "approved"` AND triple-disagreement.

**Why:** WARN allows hidden mis-placement to ship. BLOCK forces honesty.

**Phase:** B.0 (axiom backfill tool)

---

## Tweak 2 — Resolve ledger/base-axiom double-count ambiguity (§1.13)

**Problem:** v3 §1.13 says "axiom backfill writes to delta ledger" AND "staging = base axioms + all deltas". If both are true, the same solver gets indexed twice (once from base axioms scan, once from axiom_backfill ledger entries).

**v3.1 decision — adopt PREFERRED pattern (ledger-only):**

```text
configs/axioms/*.yaml is the source material (read-only input)
backfill generates deterministic axiom_backfill ledger entries from axioms
manifest builder consumes ledger entries only
staging = all valid ledger entries with seq <= H
```

So:
- `configs/axioms/` is never indexed directly.
- Backfill tool reads axioms, emits ledger entries to `data/faiss_delta_ledger/<cell>/<seq>_axiom_backfill_<ts>.jsonl`.
- Manifest builder reads ledger only. No "base axioms" concept.

**v3 sections to update:** §1.13 (clarify), §1.16 (disaster recovery becomes "axioms → ledger → staging"), §B.0 (write ONLY to ledger).

**Phase:** B.0 prerequisite — must be settled before backfill code is written.

---

## Tweak 3 — Embedding cache normalization must match actual embedding input (§1.15)

**v3 text (current):**
```python
normalized = query.strip().lower()
```

**Problem:** If the system embeds the original query but caches under lowercased text, `"USA"` and `"usa"` share a vector incorrectly. Same for codes, identifiers, mixed-language strings.

**v3.1 correction:**
```python
import unicodedata, hashlib

def canonicalize_for_embedding(query: str) -> str:
    """The exact string sent to the embedding model. Must match production embed call."""
    return unicodedata.normalize("NFC", query).strip()

def cache_key(embedding_model: str, query: str) -> str:
    embedding_input = canonicalize_for_embedding(query)
    embedding_input_hash = hashlib.sha256(embedding_input.encode("utf-8")).hexdigest()
    return hashlib.sha256(
        f"{embedding_model}|{embedding_input_hash}".encode("utf-8")
    ).hexdigest()
```

**MAGMA trace addition:**
```yaml
query_embedding_cache_hit: true
query_embedding_hash: <sha256 of canonicalize_for_embedding output>
embedding_input_normalization_version: v1   # bump if canonicalize_for_embedding changes
```

**Critical invariant:** the cache key normalization MUST equal the production embedder's input. If you change one, change both, and bump `embedding_input_normalization_version`.

**Phase:** B.7 (embedding cache implementation)

---

## Tweak 4 — CSI_progress must clamp before log() (§1.17)

**v3 text (current):** `log(component)` — undefined when component = 0; `log(1 - x)` undefined when `x = 1.0`.

**v3.1 correction:**
```python
import math

EPS = 1e-6

def clamp01(x: float) -> float:
    return max(EPS, min(1.0, x))

CSI_progress = math.exp(
    0.20 * math.log(clamp01(cell_occupancy_ratio))
  + 0.20 * math.log(clamp01(oracle_recall_at_5))
  + 0.15 * math.log(clamp01(verifier_pass_rate))
  + 0.15 * math.log(clamp01(1 - false_solver_activation_rate))
  + 0.15 * math.log(clamp01(1 - llm_fallback_rate))
  + 0.10 * math.log(clamp01(bounded_composite))
  + 0.05 * math.log(clamp01(magma_trace_completeness))
)
```

**Plus:** record unclamped components separately so dashboards can show original values:

```yaml
csi_progress: <float>
csi_progress_components:
  cell_occupancy_ratio: {raw: 0.0, clamped: 1e-6}
  ...
```

**Phase:** B.7 / Phase F (when CSI is first computed)

---

## Tweak 5 — Use parsed semver/integers for version comparison (§1.6)

**v3 text (current):**
```python
t.original_router_version >= MIN_TRUSTED_ROUTER_VERSION
```

**Problem:** If versions are strings, `"3.10"` < `"3.9"` lexically. False filter passes.

**v3.1 correction (preferred — explicit integer schema):**

Add to CaseTrajectory schema:
```yaml
router_schema_version: 7         # bump on router behavior change
verifier_schema_version: 4       # bump on verifier behavior change
```

Filter:
```python
trajectories = [t for t in case_store
                if t.router_schema_version >= MIN_TRUSTED_ROUTER_SCHEMA   # int comparison
                and t.verifier_schema_version >= MIN_TRUSTED_VERIFIER_SCHEMA]
```

**Acceptable fallback (semver if router_schema_version unavailable on legacy traces):**
```python
from packaging.version import Version

trajectories = [t for t in case_store
                if Version(t.original_router_version) >= Version(MIN_TRUSTED_ROUTER_VERSION)]
```

**Phase:** B.5 / Phase C (when oracle truth set is built from trajectories)

---

## Tweak 6 — Phase A.6 Ollama restart must be platform-safe (§1.12)

**v3 text (current):**
```python
restart_ollama_via_systemd()
```

**Problem:** Repo runs on Windows (i7-12850HX, Python 3.13). systemd doesn't exist on Windows. Test fails for the wrong reason.

**v3.1 correction:**
```python
import platform

def restart_ollama_or_skip() -> dict:
    sys_name = platform.system()
    if sys_name == "Linux":
        try:
            subprocess.check_call(["systemctl", "restart", "ollama"])
            return {"restarted": True, "method": "systemd"}
        except Exception as e:
            return {"restarted": False, "method": "systemd", "error": str(e)}
    elif sys_name == "Windows":
        # Try named service if present; otherwise skip with reason
        try:
            subprocess.check_call(["sc", "stop", "Ollama"])
            subprocess.check_call(["sc", "start", "Ollama"])
            return {"restarted": True, "method": "windows_service"}
        except Exception as e:
            return {"restarted": False, "method": "windows_service",
                    "skip_reason": "Ollama runs as user process, not service",
                    "error": str(e)}
    return {"restarted": False, "method": "none",
            "skip_reason": f"unsupported platform {sys_name}"}
```

**Determinism test result schema (new):**
```yaml
embedding_determinism:
  same_process_determinism: pass | fail
  post_restart_determinism: pass | fail | skipped_with_reason
  cosine_drift_p95: <float>
  rounded_hash_drift: <int>
  restart_method: systemd | windows_service | none
```

If `same_process_determinism` is pass but `post_restart_determinism` is `skipped_with_reason`, Phase A.6 still passes.

**Phase:** A.6 (embedding determinism gate)

---

## Tweak 7 — Snapshot acquisition as context manager + leak metrics (§1.14)

**v3 text (current):**
```python
def hybrid_retrieve(query):
    snapshot = faiss_registry.current_snapshot()
    trace.faiss_manifest_version = snapshot.manifest_version
    ...
```

**Problem:** Exception in any cell search pins the snapshot forever (refcount never released).

**v3.1 correction:**
```python
from contextlib import contextmanager

@contextmanager
def snapshot(self):
    snap = self._acquire()  # increments refcount
    try:
        yield snap
    finally:
        self._release(snap)  # decrements refcount, frees if 0

def hybrid_retrieve(query):
    with faiss_registry.snapshot() as snap:
        trace.faiss_manifest_version = snap.manifest_version
        cells_to_search = decide_candidate_cells(query)
        results = []
        for cell_id in cells_to_search:
            results.extend(snap.cells[cell_id].search(query_vec, k=5))
        return rank_and_dedup(results)
    # snapshot released here even if exception thrown above
```

**Watchdog metrics added:**
```text
waggledance_faiss_snapshot_live_count
waggledance_faiss_snapshot_oldest_age_s
waggledance_faiss_snapshot_leak_warning_total  # snapshots older than 5min
```

**Alert:** if `oldest_age_s` exceeds 300, suspect leak — log full stack trace, force release with operator alert.

**Phase:** B.3 (manifest tool, where snapshot API lives)

---

## Summary

7 tweaks total. None require re-architecture. All can be applied during Phase A/B implementation.

| Tweak | Phase | Severity |
|---|---|---|
| 1. Source-side BLOCK | B.0 | High (security/honesty) |
| 2. Ledger/base-axiom resolution | B.0 prerequisite | High (must settle before code) |
| 3. Cache normalization NFC | B.7 | Medium |
| 4. CSI_progress clamp | F | Low (but tests would crash without it) |
| 5. Version semver/int | B.5 | Low |
| 6. Restart Windows-safe | A.6 | Low (test won't run otherwise) |
| 7. Snapshot context manager | B.3 | Medium (leak prevention) |

**Phase A.1 begins now.** Tweaks applied in their respective phases as listed.

Final verdict from GPT round-3: ARCHITECTURE_APPROVED. Implementation may proceed.
