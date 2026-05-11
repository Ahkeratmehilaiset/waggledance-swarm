# Boot after reboot — quick reference

**Audience**: operator + the next Codex/Claude session that opens a fresh
shell after the laptop reboots, OOMs, or needs to recover bridge state.

**Authoritative deeper doc**: `.agent-bridge/BOOTSTRAP.md`. This file is the
quick reference — open this first, then go to BOOTSTRAP.md if anything
unusual.

---

## 1. The one-line bootstrap

```powershell
cd C:\Python\project2-master

# If the previous session crashed (BSOD / Ctrl+C close on host / OOM),
# clear orphaned background jobs first:
.\.agent-bridge\bin\Stop-AgentBridgeSession.ps1

# Bootstrap the bridge — DOT-SOURCE so env vars stay in this shell:
. .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 -Agent codex
```

Replace `-Agent codex` with `-Agent claude` for a Claude shell. Both can
run in the same primary repo for read-only review; for parallel write work
each agent should be in its own worktree (see Section 4).

What this single command does automatically:

- Sets `$env:AGENT_BRIDGE_RUNTIME_ROOT` = `C:\Python\project2-master\.agent-bridge`
- Sets `$env:AGENT_BRIDGE_RUN_ID` = `<agent>-<timestamp>`
- Emits a `liveness/active` event so other agents see this shell come online
- Prints `Read-AgentBridge -Tail 80` — the last 80 bridge events, including
  open claims, foreign writes, finding/decision events
- Starts the **R23.0 wake-watcher** background job (push-style coordination,
  ~200 ms reaction to incoming events)
- Starts the **R23.1 heartbeat** background job (60 s liveness pulses so
  the agent's claims do not auto-expire under stale-lease)
- Registers the **R23.1.1 PowerShell.Exiting cleanup** handler (background
  jobs stop cleanly when this shell exits normally)

## 2. Set Anthropic key BEFORE starting if you want R22.3 to unblock

Codex's PR #211 is `blocked/waiting_secret_env`. To resume R22.3 Profile L
A/B testing immediately after reboot:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
. .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 -Agent codex
```

Without the key, all other work continues normally; R22.3 just stays blocked.

## 3. What survives a reboot (where memory lives)

| Location | Purpose | Tracked? |
|---|---|---|
| `.agent-bridge/shared/events.jsonl` | Authoritative event log between Claude + Codex | gitignored, machine-local |
| `.agent-bridge/work_queue/` | Active claims + done/stale/superseded archive | gitignored, machine-local |
| `iterations/codex_scout_tasks/*.md` | All scout-RFCs, drafts, decision artifacts | gitignored, machine-local |
| `.codex-audit/` | Codex's audit + measurement runs (DBs, manifests, drafts) | gitignored, machine-local |
| `C:\Users\mfi0jjko\.claude\projects\C--Python-project2\memory\` | Claude's auto-memory (MEMORY.md + topic files) | machine-local |
| `CHANGELOG.md`, `README.md`, `CURRENT_STATE.md`, `EVOLUTION_INDEX.md` | What's measurably true on main | tracked on GitHub |
| `gh release view <tag>` body | Per-tag summary, post-release activity, anti-claims | GitHub |
| `gh pr list --state open` | Open work-in-flight | GitHub |

**Implication**: if the laptop is wiped or replaced, only the GitHub-tracked
files come back; iterations/scouts and bridge state are lost. The current
machine has all of it.

## 4. Per-agent worktrees (parallel write)

If Codex and Claude both need to write at once, neither should run from the
primary repo. From `C:\Python\project2-master`:

```powershell
$wt = & .\.agent-bridge\bin\New-AgentBridgeWorktree.ps1 `
  -Agent codex `
  -TaskId "codex-session-$((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'))" `
  -Base origin/main

cd $wt.worktree_path
. .\.agent-bridge\bin\Start-AgentBridgeSession.ps1 -Agent codex -RequireDedicatedWorktree
```

Both worktrees write to the same `.agent-bridge/` runtime root, so claims
and events stay synchronized.

## 5. Open development findings (post-2026-05-11)

These are the topics Codex / Claude / operator should expect to resume on
the next session. Each line points to where the deeper context lives.

### Currently open

- **R22.3 Profile L Anthropic A/B (PR #211)** — Codex blocked waiting for
  `ANTHROPIC_API_KEY` env. Once set, runs cloud A/B against the hex-aligned
  oracle. After it merges, the R22.5 stable cut path is unblocked.
- **R22.5 stable v3.12.0 promotion** — target ≥ **2026-05-24** (14-day soak
  ends + R22.3 lands). Promotion criteria checklist in
  `iterations/codex_scout_tasks/r22_5_promotion_criteria_scout_2026_05_10.md`.
  The stable-promotion workflow `.github/workflows/release-docker-stable.yml`
  is now on main (PR #221, merged 2026-05-11) so the cut is a one-click
  `gh workflow run` away.
- **Option B production PR (Codex in-flight)** — `r22-option-b-read-connections-2026-05-11`
  active claim in Codex worktree at `C:/tmp/.../codex-r22-option-b-read-connections-2026-05-11`.
  Implements the measured thread-local read-only `sqlite3.Connection`
  + `query_only=ON` PRAGMA pattern that Claude's spike validated at
  313–1 028× routing-read improvement and full 8 258-test suite PASS.
  Write scope `waggledance/core/storage/control_plane.py` + 2 test files.

### Recently closed (2026-05-10 + 2026-05-11)

- **R22 branch-isolation stress measurement track** (Run A/B/C/D + Run E
  read-path + Run F routing hot-path + Option B spike) — full data in
  `.codex-audit/branch_isolation_stress_2026_05_10/SUMMARY.md`; recorded
  in `iterations/EVOLUTION_INDEX.md` via PR #220 (2026-05-11).
- **R22.5 release-docker-stable.yml workflow** — PR #221 merged 2026-05-11.
- **EVOLUTION_INDEX substrate measurement entries** — PR #219 (solver +
  live-agent capacity) and PR #220 (branch-isolation stress) both merged
  2026-05-11.
- **PR #216 README "myyvempi" with Codex's measured numbers** — merged
  2026-05-10.
- **Orphan worktree `.codex-audit/wd-pr191-fix`** — 88 MB stale directory
  cleaned 2026-05-11 (residual `.pytest_cache` subdir locked, harmless
  disk-space only; operator-side cleanup on next reboot).

### Operator decisions awaiting

1. **R22.x Finnish→English agent contract migration** — Option A / B / C?
   Recommended Option B (full English baseline + `agents_locale/fi/` overlay).
   Sized at 42–50 FTE hours, 2.1–2.6k LoC diff, 3–4 weeks calendar. AFTER
   R22.5 stable cut. Scout: `iterations/codex_scout_tasks/r22_x_finnish_to_english_agent_contract_scout_2026_05_10.md`.
2. **R25 3D hex topology + per-cell DB sharding** — **architectural
   recommendation UPDATED 2026-05-11 by the Run A–F + Option B spike**:
   R25 is now a *write-contention-only* decision. The earlier "12.2×
   degradation under adversarial load" framing covered only one of three
   contention regimes; the measurement track exposed cross-table routing
   degradation (2 494× at N=6 concurrent writers) and same-table read
   degradation (128× at N=6) which Option B (thread-local read connection
   + WAL MVCC) fixes at ~10–30 LoC. The recommended ordering is **(1)
   land Codex's Option B PR + measure production traffic, (2) THEN decide
   R25** based on write-side p99 alone. Full Run A–F + Option B numbers in
   `.codex-audit/branch_isolation_stress_2026_05_10/SUMMARY.md` and
   `iterations/EVOLUTION_INDEX.md` (entry
   `r22-2d-branch-isolation-stress-inflection`). Codex's 12-document
   scout pack at `iterations/codex_scout_tasks/r25_*_codex_*.md` is
   reference material — don't implement until the Option B path lands.
3. **Dependabot #21 (checkout 4→6) + #26 (psutil patch)** — low-risk
   hygiene cleanup, awaiting operator approve/defer signal. The other 5
   dependabot PRs (#19/#22/#23/#24/#25) deferred per the audit.
4. **Dockerfile entrypoint canonicalization** — three different bootstraps
   coexist (Dockerfile CMD vs docker-compose vs pyproject console-script).
   Operator opinion needed on canonical for v3.12.0.
5. **`KUSTANNUSLASKELMA.md` placement** — kept on main as marketing
   context after R22.5 cleanup; operator can decide separately whether to
   move to `docs/marketing/`.

### Recently closed (for context)

- 2026-05-10 morning sprint: R22.0/R22.1a/R22.2/R22.x silent-bug-sweep
  + R23.0 wake-on-event + R23.1 heartbeat + R23.2 worktrees + R23.1.1
  orphan-job cleanup + visibility refresh — see `CHANGELOG.md` "[R22 + R23
  Phase D scaling refinements + autonomy fabric]" entry
- 2026-05-10 cleanup: 26 stale tracked files removed (PR #213 + PR #215),
  including 5 Murata employee email addresses in `docs/send_email*.py`
  (PII finding, operator decision (a) = git-history retained per
  bridge task `claude-pii-finding-pr213-2026-05-10`)
- Codex live-agent-capacity audit: 81 templates, 1004 clones / 120 s,
  concurrent warm think 0.47 s. Bottleneck: per-agent SQLite commits +
  Finnish prompt contract. Mitigated by PR #214 fi→en→fi adapter
  (adapter-only, NOT yet wired into Agent.think).

## 6. Verification commands (smoke tests)

After bootstrap, verify substrate is healthy:

```powershell
# R23.0 wake-on-event smoke (9 checks)
.\.agent-bridge\bin\Test-BridgeWakeOnEventSmoke.ps1

# R23.1.1 orphan-job cleanup smoke (12 checks)
.\.agent-bridge\bin\Test-BridgeJobCleanupSmoke.ps1

# R13 runtime root contract (10 checks)
.\.agent-bridge\bin\Test-BridgeRuntimeRootSmoke.ps1

# R15 stale-lease auto-release (10 checks)
.\.agent-bridge\bin\Test-BridgeStaleLeaseSmoke.ps1

# R23.2 worktree isolation (8 checks)
.\.agent-bridge\bin\Test-BridgeWorktreeIsolationSmoke.ps1
```

All five should pass. If any fail, do not assume the bridge is healthy.

## 7. Shutdown discipline

When closing a Codex/Claude shell normally (typing `exit` or just closing
the host), the R23.1.1 `PowerShell.Exiting` handler stops both the wake-watcher
and heartbeat jobs cleanly. **Hard kills (BSOD, OOM, kill -9) do not
trigger the handler** — orphans accumulate. Run
`Stop-AgentBridgeSession.ps1` at the next session start (or any time) to
clear them.

## 8. Where to read more

- `.agent-bridge/BRIDGE_PROTOCOL.md` — full coordination protocol
- `.agent-bridge/BOOTSTRAP.md` — fuller reboot runbook (this file's source)
- `CLAUDE.md` — operator rules for Claude Code
- `AGENTS.md` — task rules
- `CHANGELOG.md` — round-by-round change history
- `CURRENT_STATE.md` — auto-generated project state (regenerate via
  `python tools/generate_state.py`)
- `iterations/EVOLUTION_INDEX.md` — Axis A/B/C metrics per round
- `iterations/codex_scout_tasks/*.md` — all 30+ scout-RFCs from 2026-05-10

---

*Generated 2026-05-10 as part of the visibility-fix track. Update this
file when the bootstrap commands or open-decisions list materially change.*
