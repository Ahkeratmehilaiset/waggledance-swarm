# Phase 16B P8 — Dependency / install / API-surface truth

## Install-source layering

The repository carries **three install sources**, each with a documented purpose:

| file | purpose | who uses it |
|---|---|---|
| `requirements.txt` | top-level / loose pin | developer local install (`pip install -r requirements.txt`); README quickstart |
| `requirements.lock.txt` | full lock file (every transitive pinned) | Docker image build; `RELEASE_READINESS.md` reproducible-from-clone instructions |
| `requirements-ci.txt` | minimal subset (no `faiss-cpu`, no Playwright) | GitHub Actions CI (`Tests` and `WaggleDance CI` workflows) |

This layering is **intentional** — different consumers have different reproducibility / speed trade-offs:

* **Developer onboarding** wants the fastest install; `requirements.txt` resolves transitives at install time.
* **Reproducible release** wants byte-identical installs; `requirements.lock.txt` pins everything.
* **CI** wants fast green-or-fail loops without heavyweight optional deps; `requirements-ci.txt` skips `faiss-cpu` and Playwright.

## Where each install command appears

| source | file | line | command |
|---|---|---|---|
| README quickstart | `README.md` | ~257 | `pip install -r requirements.txt` |
| reproduce-from-clone (RELEASE_READINESS) | `docs/release/RELEASE_READINESS.md` | ~24 | `pip install -r requirements.lock.txt` |
| Docker build | `Dockerfile` | 23 | `RUN pip install --no-cache-dir -r requirements.lock.txt` |
| CI workflow | `.github/workflows/ci.yml` | 28 | `pip install -r requirements-ci.txt` |

These are **consistent given the layering**. They are not contradictory.

## Stable gate disposition (g16)

**Truthful documentation of the layered model is sufficient.** The master prompt says:

> If install commands disagree between README, RELEASE_READINESS, Docker docs, and CI, stable v3.8.0 is blocked until the inconsistency is fixed or **explicitly documented**.

P9 release docs update will add a short paragraph to `RELEASE_READINESS.md` cross-referencing this document so an external GitHub reader sees the layered model immediately.

g16 disposition: **PASS** for prerelease and stable, conditional on the P9 documentation paragraph.

## HTTP / API route stable gate decision

### Decision

**`v3.8.0` is a library / service-layer stable release, NOT an HTTP-API stable release.**

The autonomy lane is reachable in production via:

* `waggledance.application.services.autonomy_service.AutonomyService.handle_query(query, context, priority)` — Phase 16A's selected upstream caller, exposed as a Python API.
* The internal call chain from there reaches `AutonomyRuntime.handle_query` (Phase 15 wiring) → `SolverRouter.route` (Phase 14 autonomy consult) → `RuntimeQueryRouter` + `HotPathCache` (Phase 13/14).

The autonomy lane is **not** reachable via a dedicated HTTP route — there is no `/api/autonomy/query` endpoint in `waggledance/adapters/http/routes/autonomy.py`. The existing `/api/autonomy/*` routes expose status, KPIs, learning-cycle control, proactive goals, and safety-case introspection only.

### Why this decision

* Adding a new HTTP route in this stabilization phase is out of scope — it would be a feature, not a stabilization.
* The Python service-layer caller is the authoritative production seam; HTTP routing is a thin presentation layer that any deployment can add at the edge.
* Phase 16A explicitly documented that `/api/autonomy/query` is **not implemented** (see `docs/runs/phase16_upstream_structured_request_2026_05_01/upstream_structured_request_proof.md` and the Phase 16A truth-map update). That non-implementation is not a regression; it is a deliberate scope limit.

### What this means for the stable claim

`RELEASE_READINESS.md` (post-P9 update) will state explicitly:

> **v3.8.0** (and any equivalent stabilization tag) is a Python library / service-layer stable release, not an HTTP-API stable release. Production callers integrate via `AutonomyService.handle_query(query, context, priority)`. The FastAPI surface for query-by-HTTP is not in scope for this release line.

g17 disposition: **PASS for prerelease and stable** once the P9 documentation paragraph lands.

## Summary

| gate | result |
|---|---|
| g16 (install commands consistent) | **PASS** with documented layering |
| g17 (HTTP route stable decision) | **PASS** — service-layer-stable, not HTTP-stable |
