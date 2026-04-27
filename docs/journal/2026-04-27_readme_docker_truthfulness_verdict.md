# 2026-04-27: README / CURRENT_STATUS / Docker / startup truthfulness verdict

Performed under WD `Claude_Code_unified_release_finalization_prompt_v3.md` Phase 5.

## Verdict summary

| Surface | Pre-update truthfulness | Action taken | Post-update verdict |
|---|---|---|---|
| `README.md` | Already truthful (rewritten in v3.6.0 release as commit `a00858d`) | None | ✅ Truthful |
| `CURRENT_STATUS.md` | STALE — claimed v3.5.7, main at `ddd13e7`, campaign 175.19h / 43.8% (active) | Updated head + 400h section + added Phase 9 release section + Phase 8.5 deferral table | ✅ Truthful |
| `CURRENT_STATE.md` | STALE — generated 2026-04-21 against commit `ddd13e7` | Regenerated via `python tools/generate_state.py` | ✅ Truthful |
| `Dockerfile` | Already truthful | None | ✅ Truthful |
| `docker-compose.yml` | Already truthful | None | ✅ Truthful |
| `start_waggledance.py` | Already truthful (preset choices match docs) | None | ✅ Truthful |
| `waggledance/adapters/cli/start_runtime.py` | Already truthful (--help works, args match docs) | None | ✅ Truthful |

## Detail by file

### README.md — already truthful
- Reality View claims (11 panels, never-fabricate, 5/11 evidence-render) verified against `waggledance/ui/hologram/reality_view.py` (`PANELS` tuple has 11 entries, `_unavailable_panel()` for missing data, no fabricated values in tests)
- Phase K hex topology claims (4 live_states, 4 subdivision_states, 8 cells) verified against `waggledance/core/hex_topology/cell_runtime.py` (`LIVE_STATES`, `SUBDIVISION_STATES`)
- BUSL Change Date `2030-03-19` matches `LICENSE-BUSL.txt`
- All 16 Phase 9 phases F–Q listed match `docs/architecture/PHASE_9_ROADMAP.md`
- "Final atomic runtime flip — separate session" section honestly disclaims that v3.6.0 ships scaffold only, not live runtime
- "What this is not" section explicitly disclaims auto-merge, auto-deploy, producer-side completeness
- "Why the name WaggleDance?" section preserves bee origin honestly while marking new code as domain-neutral

### CURRENT_STATUS.md — was STALE; updated
**Before:**
- Header said v3.5.7 + post-release hardening, main at `ddd13e7`, updated 2026-04-21
- 400h campaign section claimed 175.19h / 400h (43.8%, active, "no version bump planned until campaign completes")
- No mention of Phase 9, no mention of v3.6.0 release

**After:**
- Header: v3.6.0 (Phase 9 Autonomy Fabric scaffold), main at `a1c4152`, tag v3.6.0, updated 2026-04-27
- New "Latest release — v3.6.0" section describing 16-phase scope, 657/657 tests, evidence artifacts, links to roadmap
- 400h campaign section updated to FINAL with cumulative 415.34h / 400h (103.8%), 60 807 queries, 0 XSS / 0 DOM-break
- New "Phase 8.5 producer subsystems — deferred" section with 5-row table mapping each branch to follow-up PR plan
- New "Next post-release step" pointer to follow-up PR sequence + Prompt 2 atomic flip
- "Atomic flip" status disclaimed: "review-only release", explicit pointer to `HUMAN_APPROVAL.yaml.draft` gating

### CURRENT_STATE.md — was STALE; regenerated
**Before:** Generated 2026-04-21T07:47:05+0300 against commit `ddd13e7`
**After:** Regenerated 2026-04-27T16:28:29+0300 against commit `a1c4152` on `feature/post-v3.6.0-truthfulness`. Tool `python tools/generate_state.py` ran cleanly (`Generated C:\Python\project2-master\CURRENT_STATE.md (323 lines)`), output reflects current reality.

### Dockerfile — already truthful
- `CMD ["python", "start_waggledance.py"]` matches README's "Quick start" Docker section
- `EXPOSE 8000` matches `Dashboard: http://localhost:8000` claim
- `ENV OLLAMA_HOST=http://host.docker.internal:11434` matches "Ollama runs outside container" comment
- Healthcheck via `/health` endpoint (provided by `waggledance/adapters/http/routes/`)

### docker-compose.yml — already truthful
- Two services (ollama with NVIDIA GPU reservation + waggledance with build: .) match the documented two-entry-point setup in `docs/runs/docker_validation.md`
- Compose-only command override `python -m waggledance.adapters.cli.start_runtime` is documented as production-style invocation honestly

### start_waggledance.py — already truthful
- `--preset` choices `[raspberry-pi-iot, cottage-full, factory-production]` match README's "### Native" examples
- `--stub` flag exists and matches README documentation
- `--help` works cleanly

### waggledance/adapters/cli/start_runtime.py — already truthful
- `--help` returns: `[--stub] [--host HOST] [--port PORT] [--log-level {debug,info,warning,error,critical}] [--check-autonomy]`
- Module imports cleanly (`from waggledance.adapters.cli import start_runtime` succeeded in earlier verification)
- Description "WaggleDance AI runtime (hexagonal architecture)" matches README's hexagonal layout claims

## What was NOT changed

- Phase 9 release-bundle docs (`docs/runs/release_bundle_2026_04_27/`) — already truthful as committed
- README.md — left intact
- Dockerfile — left intact (would only change if there were a real bug)
- docker-compose.yml — left intact (correctly documented as two-entrypoint reality)
- Both startup scripts — left intact

## Why this is its own follow-up branch (not on phase9/autonomy-fabric)

PR #51 is already merged. Per `Claude_Code_unified_release_finalization_prompt_v3.md` §4.5:

> If these truthfulness fixes are not already in main after Phase 2 and are not appropriate for phase9/autonomy-fabric, create a small follow-up branch from main: `feature/post-v3.6.0-truthfulness` and handle it as a small, self-contained PR.

Branch created: `feature/post-v3.6.0-truthfulness` from `main @ a1c4152`.

## Strategy A compliance

- ✅ No history rewrite
- ✅ No force-push
- ✅ Minimal scope: only stale truthfulness corrections
- ✅ Domain-neutral (no bee/swarm metaphor introductions)
- ✅ No overclaim (does not imply atomic flip is done)
- ✅ Honest about what v3.6.0 ships (scaffold) vs what is deferred (atomic flip, Phase 8.5 producers)
