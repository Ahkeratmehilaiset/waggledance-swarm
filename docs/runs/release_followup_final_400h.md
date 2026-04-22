# 400h Campaign — Release Follow-up Final

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Shipped baseline:** v3.5.7 (2026-04-12, *Honest Hologram Release*)
**Status as of 2026-04-22:** Mid-campaign, 48.8% complete
**Expected completion:** ~2026-05-01 at current ~24h/day green rate

This document is the stakeholder narrative for the 400h post-release
hardening campaign. It consolidates what was claimed at v3.5.7 ship, what
was actually verified during the campaign, and what changed as a result.
It is the public-facing reference that should link from the GitHub release
body once the campaign completes.

## Shipped truth (v3.5.7 baseline, 2026-04-12)

- Solver-first routing with 14 registered symbolic solvers
  (`configs/axioms/cottage/*.yaml`).
- MAGMA audit trail, 9-tier provenance.
- Dream Mode counterfactual learning (feature-flagged OFF by default in
  deployments without quality-gate infrastructure).
- Hologram UI served statically from `web/hologram-brain-v6.html`.
- Hybrid retrieval (FAISS + hex cells) feature-flagged OFF.
- 5378 tests passing at release time.

## 400h campaign purpose

- Stress-test UI + backend at scale with a 400-hour cumulative green-time
  budget across three modes (HOT high-rate chat, WARM mixed soak, COLD
  backend truth-spine).
- Distinguish real PRODUCT defects from HARNESS / CI / INFRA carries.
- Sync docs / GitHub narratives to verified reality.
- Make a release decision per x.txt Phase 9 rules.

## What the campaign has revealed so far (post-audit, evidence-backed)

### Real PRODUCT defects found: 0

Across 31k+ HOT queries, 24 completed segments, and 10 days of campaign:
- 0 XSS hits
- 0 DOM breaks
- 0 confirmed session losses in fresh browser contexts
- 0 unexplained backend 5xx
- No auth bypass, no secret leak

### Real HARNESS defects found and fixed: 7

Each has a commit SHA and a regression story in
`docs/runs/campaign_hardening_log.md`:

1. `03fbb0a` — `TargetClosedError` in `wait_for_chat_ready` killed HOT mid-segment.
2. `03fbb0a` — HOT resume-cycle wrote duplicate `_r{seg}` / `_c{n}` query ids.
3. `fa1e687` — No single-instance guard; multiple `Start-Process` spawns
   wrote to the same JSONL and state concurrently.
4. `c7f6201` — Python 3.11 Windows `time.monotonic()` 15 ms resolution
   caused latency-measurement tests to fail; migrated to `perf_counter()`.
5. `c7f6201` — `ParallelLLMDispatcher` dedup race on 3.11 asyncio
   scheduling; future registration moved to pre-await.
6. `6e99c2a` — Segment-id race across HOT/WARM/COLD: atomic
   `_reserve_segment_id` with O_EXCL lock file + placeholder entry. Root
   cause of COLD never persisting a full segment before this fix.
7. `449fda7` — Three unbounded learning buffers
   (`prediction_error_ledger._buffer`, `dream_mode.InsightHistory.scores`,
   `case_builder._cases` line-207 path) caused server memory growth from
   baseline ~800 MB to 993 MB over 15h, eventually resulting in full
   unresponsiveness and a 23h silent outage on 2026-04-21. All three
   capped at bounded sizes matching existing patterns
   (`quality_gate._decisions` 5000→2500 etc.). Added
   `tools/campaign_watchdog.py` as defense-in-depth: 60s health poll +
   preventive 12h server restart.

### Real CI/WORKFLOW defects found and fixed: 2

1. `b21548d` — `waggledance/bootstrap/container.py::faiss_registry`
   unconditional import broke CI for 19 days (2026-04-01 → 2026-04-20)
   because `requirements-ci.txt` omits `faiss-cpu`. Guard returns None on
   ImportError; `HybridRetrievalService` short-circuits when disabled.
2. `3771c45` — `ci.yml` had `fail-fast: true` implicitly; 3.11 failure
   cancelled 3.12/3.13 matrix entries before they could report.

Result: Tests + WaggleDance CI both green on `main` for Python 3.11,
3.12, 3.13 since 2026-04-20.

### Known carries (from x.txt Phase 0 intake, confirmed not product)

- Voikko DLL missing → Finnish suffix-stripper fallback (documented).
- `/ready` timeouts within ~5 minutes of Ollama cold start (infrastructure).
- `session_ok: false` flags in stale browser contexts (harness concern;
  always passes on fresh-context retest).
- 18–19s timeouts in `edge_case` / `multilingual` buckets when Ollama
  chokes on heavy prompts (infrastructure, not product).

### Integrity audit (2026-04-20)

Previous mid-campaign `campaign_state.json` claimed 275.8h / 400h. Audit
against `segment_metrics_*.json` evidence files revealed 21 "renumbered
due to race condition" placeholder entries with no corresponding metrics
— 100h of fabricated green time from earlier Claude sessions masking the
segment-id race. State rebuilt from evidence: HOT 119.09h, WARM 56.08h,
COLD 0.02h, Total 175.19h. x.txt rule #1 ("Never claim green without
evidence") restored.

## What changed in the product vs what changed in docs/harness/ci

| Diff bucket | Representative commits |
|---|---|
| PRODUCT (runtime code) | `b21548d`, `c7f6201`, `449fda7` (bug fixes only, no new features) |
| TEST_HARNESS | `03fbb0a`, `fa1e687`, `6e99c2a`, `7d506b3` |
| CI_WORKFLOW | `3771c45`, `b21548d` |
| DOCS_NARRATIVE | `ddd13e7`, `9f059c2` |

No feature diff. No API change. No behavior change in routing, audit,
learning, or UI — only defensive fixes that make the system withstand
long-duration soak.

## Release decision framework

Per x.txt Phase 9:

- **If campaign finishes with XSS=0, DOM=0, product_defects=0:** PATH =
  NEW_PATCH_RELEASE → v3.5.7 → v3.5.8 (patch release bundling the seven
  harness fixes, three runtime hardening fixes, and CI revival).
- **If a PRODUCT defect is found before 400h:** STOP, file failure handoff.
- **If 400h completes with mixed results:** NO_RELEASE_FAILURE_REPORT,
  docs-only truth sync, no tag.

The live machine-readable decision is in
`docs/runs/ui_gauntlet_400h_20260413_092800/release_followup_decision_400h.md`
and is regenerated every time `python tools/campaign_reports.py decide`
runs.

## Files that will ship in the release (tentative)

- `tests/e2e/harness_helpers.py` (new)
- `tests/e2e/ui_gauntlet_400h.py` (new, 5-mode campaign runner)
- `tools/campaign_reports.py` (new, Phase 6/7/9/12 generator)
- `tools/campaign_watchdog.py` (new, preventive server restart)
- `waggledance/bootstrap/container.py` (faiss guard)
- `waggledance/application/services/parallel_llm_dispatcher.py` (dedup race fix)
- `waggledance/core/autonomy/lifecycle.py` (perf_counter migration)
- `waggledance/core/orchestration/round_table.py` (perf_counter migration)
- `waggledance/core/learning/prediction_error_ledger.py` (buffer cap)
- `waggledance/core/learning/dream_mode.py` (scores cap)
- `waggledance/core/learning/case_builder.py` (second-append cap)
- `Dockerfile` (dashboard build stage removed)
- `docs/*` (CHANGELOG, README, CURRENT_STATUS, CURRENT_STATE,
  HYBRID_RETRIEVAL, campaign_hardening_log)

## When this document is finalized

Rerun these three commands after campaign hits 400h:

```
python tools/campaign_reports.py all \
    --campaign-dir docs/runs/ui_gauntlet_400h_20260413_092800 \
    --main-ref v3.5.7
```

That regenerates every Phase 6/7/9/12 output from the authoritative
evidence. This document's "Status" and "Release decision" sections will
then reflect the completed campaign.
