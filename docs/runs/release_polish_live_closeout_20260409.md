# Release Polish Live Closeout — 2026-04-09

WaggleDance v3.5.7 Release Polish Run
Branch: `feat/v357-feed-runtime-wiring`
Run directory (outside repo): `C:/WaggleDance_ReleasePolishRun/20260409_054702/`
Final HEAD: `3723660 docs: post-gate sweep — F1-004 + soak partial + API.md endpoints`

## Verdict

**READY FOR RC, NOT UNATTENDED.**

Not tagged. Not pushed. Not merged. `pyproject.toml` stays at
3.5.6 intentionally. The one reason this is not "READY FOR
RELEASE" is that the x.txt Phase 5 long-soak target of 12-18 h
unattended was not met — a real **56.7-minute** continuous mixed-
workload soak was run instead, with **4 942 requests** against the
real server and **zero** errors, zero conn errors, zero 5xx, and
**+4.11 MB RSS drift**. That window is favourable but not
overnight.

Everything else is green.

## One-page summary

| Area | Status | Evidence |
|---|---|---|
| Branch state | 23 local commits, clean working tree, not pushed | `git log --oneline` |
| Pytest | **5358 passed, 3 skipped, 0 failed** (587.86 s) | baseline 5191, gate 5234, net +167 |
| Wheel | `waggledance_swarm-3.5.6-py3-none-any.whl` (238 files, 454 835 bytes) | `pip wheel --no-deps .` succeeded |
| Live smoke (Phase 6) | **13/13 PASS** against real server on `:8001` | `reports/phase6_live_smoke.log` |
| Live smoke (Phase 1) | 13/13 PASS on first bring-up | `reports/LIVE_SERVER_SMOKE.md` |
| Health contract | 5 live states documented, 6-question operator decision tree | `reports/HEALTH_PROBE_CONTRACT.md` |
| Recovery drills | 5/5 drills produced operator-legible output | `reports/RECOVERY_DRILL.md`, `recovery_drills/*.log` |
| Long soak | **PARTIAL** — 56.7 min, 4 942 requests, 0 errors, +4.11 MB RSS | `reports/LONG_SOAK_FINAL.md` |
| Docs sweep | CHANGELOG, README (5358 badge), docs/API.md (4 new endpoints) | commit `3723660` |
| Version bump | **NOT applied** (gate not fully green) | `pyproject.toml` unchanged |
| Secrets | 0 leaks, `repr(WaggleSettings)` no-leak invariant locked by 8 tests | `tests/test_config_auth_precedence.py` |

## What this run closed

### Live-server-dependent items (the whole point of this run)

- **F1-004 Ollama probe timeout false-negative** — closed with a
  full rewrite: `_check_ollama()` now returns `(ok, reason)`,
  normalised into short operator strings (`"timed out"`,
  `"connection refused"`, `"HTTP 503"`, …), timeout bumped 3 → 5 s
  with an env-var override (`WAGGLE_OLLAMA_PROBE_TIMEOUT`) and
  guard rails (reject `<=0`, `>60`, non-numeric). 23 new tests
  in `tests/test_ollama_probe.py`. Commit `90b3956`.
- **F2-003 health probe contract** — closed at contract level.
  All 5 live states A-E (happy path, cold boot, auth fail,
  Ollama down, metrics isolation) documented with real curl
  output. The shipped 6-endpoint layout (`/health`, `/ready`,
  `/healthz`, `/readyz`, `/version`, `/metrics`, plus
  `/api/status`, `/api/ops`) already answers every operator
  question unambiguously; no code change required.
- **F6-001 recovery ergonomics** — closed via 5 deliberate
  drills:
  1. Port in use (WinError 10048) — clean uvicorn error
  2. Missing / wrong Bearer — distinct `missing_credentials`
     vs `invalid_credentials` reason codes + RFC-compliant
     `WWW-Authenticate` header
  3. Ollama unreachable — the F1-004 banner lands with the
     exact reason and env-var hint
  4. `--preset=<unknown>` — argparse rejection, exit 2, usage
     line
  5. Malformed preset YAML — exact file/line/column error,
     fall back to defaults, server still starts

  No tracebacks. No silent failures. No code change required.

### Non-live items already closed pre-gate (for reference)

- Feed runtime wiring (F-NEWS-001): DataFeedScheduler into hex runtime
- cwd-independence: settings load from any working directory
- `/api/chat` `message` alias (F2-002)
- 401 ergonomics + RFC 6750 `WWW-Authenticate` header (F2-002)
- `.env` parser rewrite (F3-001): quoting, comments, BOM, `export`
- `/version` endpoint (F2-001)
- `/metrics` endpoint (F5-002): 15 counters + 2 gauges + liveness
- `--preset=<name>` plumbing (F1-006)
- Startup ergonomics: CLI args echo + direct `chcp.com` call
- Wheel build (F4-001): explicit `[tool.setuptools.packages.find]`
- HIGH: `repr(WaggleSettings)` api_key leak (F3-HIGH)
- Collection-lookup bug in `_get_chroma_collection` (F-NEWS-002)

## What this run did NOT close

- **The overnight 12-18 h soak.** The real blocker. A real but
  reduced 56.7 min soak ran against a real server with real
  LLM; that's enough to say "the post-gate commits did not
  regress stability inside a 1-hour window", but it is NOT
  enough to say "safe to leave unattended overnight".
- **OpenClaw v1.4 embedded subproject** (`build.py` + 13
  `tools/` scripts + 81 `agents/` directories + `output/`).
  Wheel ships zero bytes from it. Developer-tool impact is
  limited to `python -m build` from the project root, and
  `pip wheel .` (the documented path) sidesteps the problem.
  Removal needs explicit owner sign-off and is out of scope.
- **Hologram chat 401 beyond the `message` alias case.** The
  alias fix handles OpenAI-compat clients; any remaining
  browser-side hologram UI 401 is a separate follow-up item.

## Deliberate non-actions in this run

Things that were *considered* and *rejected*:

- Auto-deleting the OpenClaw subproject: rejected per x.txt hard
  limit ("ei OpenClaw-subprojektin poistoa/muokkausta ilman
  erillistä lupaa").
- Bumping `pyproject.toml` to 3.5.7: rejected per x.txt gate
  rule ("älä bump versionumeroa ennen kuin live gate on
  oikeasti vihreä").
- Restarting the soak server to retry the harness PID-detection
  bug: rejected because it would discard the accumulated soak
  evidence. The harness was fixed for future runs and an
  out-of-band `rss_manual.ndjson` was captured instead. See
  `reports/LONG_SOAK_FINAL.md` for the authoritative RSS series.
- Force-closing the "long soak" as green: rejected. Honest
  "RC NOT UNATTENDED" beats a false "READY FOR RELEASE".

## Next step for the next run

One commit, one action, one evidence file:

1. Run the overnight 12-18 h soak with the corrected harness.
2. Write `reports/LONG_SOAK_OVERNIGHT.md`.
3. Bump `pyproject.toml` version `3.5.6 → 3.5.7`. Verify
   `/version` reflects it live.
4. Update `CHANGELOG.md` header `[3.5.7] — UNRELEASED` →
   `[3.5.7] — <date>`.
5. Tag `v3.5.7`. Push. Open PR. Release.

Do **not** conflate any of the above into a single commit.

## Pointer files

- `C:/WaggleDance_ReleasePolishRun/20260409_054702/reports/FINAL_RELEASE_CLOSEOUT.md` — the long form
- `C:/WaggleDance_ReleasePolishRun/20260409_054702/GATE_DECISION.md` — the one-line gate
- `C:/WaggleDance_ReleasePolishRun/20260409_054702/progress.md` — phase matrix
- `C:/WaggleDance_ReleasePolishRun/20260409_054702/RELEASE_NOTES_v3.5.7_DRAFT.md` — user-facing notes
