# 400h Campaign — Latest Pointer

**Active campaign:** `ui_gauntlet_400h_20260413_092800`

**Directory:** `docs/runs/ui_gauntlet_400h_20260413_092800/`

**Phase:** 4 — Running (HOT/WARM/COLD loop mode, self-paced)

**Last update:** 2026-04-21

## Honest status (evidence-based, post-audit)

| Mode | Cumulative | Target | % |
|---|---|---|---|
| HOT | 119.09h | 80h | ✅ exceeded |
| WARM | 72.11h | 120h | 60% |
| COLD | 0.02h | 200h | 0% (race bug blocked COLD until 2026-04-21; first real segment in progress) |
| **Total** | **191.22h** | **400h** | **47.8%** |

Numbers derive from 40 committed segment entries in `campaign_state.json`,
each backed by a `segment_metrics_*.json` file or
`hot_results.jsonl` row evidence. Earlier 275.8h claim was inflated by
21 "renumbered due to race condition" placeholder entries (~100h) that
had no metrics evidence — see audit below.

## Acceptance

- **XSS hits:** 0 (across 22 177 HOT queries)
- **DOM breaks:** 0
- **Real session losses confirmed via fresh context:** 0 (stale-context
  session_ok=false flags are harness carries, not product)
- **Product defects:** 0
- **Harness defects found and fixed:** 7 (see CHANGELOG "Unreleased" block)
- **CI/workflow defects found and fixed:** 2 (`faiss_registry` unconditional
  import → 19 days red; `fail-fast: true` matrix)

## Major campaign events

- **2026-04-13:** Phase 0 truth intake, Phase 1 harness hardening.
- **2026-04-18:** `TargetClosedError` + duplicate-write + pidfile lock fixes.
- **2026-04-20:** Honest state audit. 21 fabricated "renumbered due to race"
  segments removed from `campaign_state.json`, ~100h of claimed green
  time revealed as placeholders with no evidence. CI revived (first green
  on `main` in 19 days) after `waggledance/bootstrap/container.py`
  `faiss_registry` guard.
- **2026-04-21:** Atomic `segment_id` reservation (O_EXCL lock + placeholder
  entry). Fixes root cause of COLD never persisting a segment — HOT/WARM
  had been stealing its id mid-segment.

## Files

| File | Purpose |
|---|---|
| [plan.md](ui_gauntlet_400h_20260413_092800/plan.md) | Campaign plan |
| [truth.md](ui_gauntlet_400h_20260413_092800/truth.md) | Phase 0 truth intake |
| [runbook.md](ui_gauntlet_400h_20260413_092800/runbook.md) | Operator runbook |
| [campaign_state.json](ui_gauntlet_400h_20260413_092800/campaign_state.json) | Machine-readable state (post-audit) |
| [hot_results.jsonl](ui_gauntlet_400h_20260413_092800/hot_results.jsonl) | HOT per-query results (append-only, authoritative) |
| [incident_log.jsonl](ui_gauntlet_400h_20260413_092800/incident_log.jsonl) | Classified incidents (PRODUCT/HARNESS/CI/INFRA/OPERATOR) |
| `segment_metrics_NNN.json` | Per-segment summary (evidence files) |
| `segment_report_NNN.md` | Per-segment human-readable report |
| [../campaign_hardening_log.md](campaign_hardening_log.md) | Chronological hardening + CI-revival narrative |

## Known carries (per `x.txt`, not product bugs)

- Voikko DLL missing → Finnish suffix-stripper fallback (documented).
- COLD `/ready` timeout within ~5 min of Ollama cold start (documented).
- `responded: false` + 18-19s latency in edge_case / multilingual
  buckets when Ollama chokes on heavy prompts (infrastructure, not
  product).
- `session_ok: false` in stale browser contexts (harness concern,
  survives fresh-context retest = always passes).
