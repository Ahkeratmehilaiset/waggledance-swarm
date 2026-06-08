# Release Readiness - WaggleDance

This file summarizes the current release state. Historical per-phase
details live in `CHANGELOG.md` and `docs/runs/*`.

## Current Status

* **Latest stable release**: `v3.8.0` (Phase 16F, released 2026-05-04).
* **Latest R21 prerelease**:
  `v3.11.0-r20-axis-b-activated-alpha` (released 2026-05-10).
* **Current mainline posture**: **v3.13.0 substrate-only landing on
  top of the v3.12.0 candidate substrate**; no package-version bump,
  no stable tag, no Docker `:latest` movement. v3.13.0 runtime
  substrate (Shadow -> Hybrid -> Autonomous migration layer) lives
  in `main` HEAD `6d2e59b` but no real-data activation has happened.
* **Next stable target**: `v3.12.0`, no earlier than 2026-05-24,
  after the R22.5 soak and promotion gates. v3.13.0 substrate does
  not move that target.
* **Docker registry**: GHCR primary. No Docker Hub mirror yet.
* **GitHub Latest**: remains `v3.8.0` until the stable promotion
  explicitly moves it.

## v3.13.0 Substrate Landing (2026-05-13)

Substrate-only landing, **NOT** a release. Runtime layer for the
Shadow -> Hybrid -> Autonomous solver migration. Full release notes:
`docs/releases/v3.13.0.md`.

Status:
* 14 PRs merged via mutual RCO (Claude + Codex), 9 round-2
  fail-closed cycles, 0 stop-condition escalations.
* Main HEAD `6d2e59b` (post #370 runbooks + #371 baseline-failclosed).
* 292 v3.13.0-bracketed tests pass (243 + 35 + 14 across module,
  contract, and tools test suites).

Modules landed (`waggledance/core/v3_13_0/`):
* `write_rco_gate.py` -- single write choke point; 12 audit event
  types; 4 risk classes; WRT-003 calls verify_solver_provenance.
* `credential_vault.py` -- refused-pickle CredentialMaterial;
  `InMemoryVault`, `OSKeyringVault`, `NoOpVault`.
* `anti_pattern_catalog.py` -- ANTI-001..007 with module-scope regex.
* `behavior_capture.py` -- OPT-IN with sensitive-class retention.
* `shadow_runner.py` -- 6 abort reasons including `BASELINE_FAILED`.
* `divergence_analyzer.py` -- 5 format comparators + INST-G09.
* `solver_provenance.py` -- signing chain + auto-quarantine +
  permanent-revoke (one-way).
* `auto_fix_loop.py` -- event consumer + repair proposer; lease.
* `defaults.py` -- DEF-001..006 constants + locale noise filters.
* `tools/sim_orchestrator.py` -- streaming-mode instrumentation.

New schema: `solver_candidate_manifest.schema.json` (SCH-005) with
`provenance_signatures` array + `activation_state` enum.

Bridge protocol: unchanged. NO new dotted bridge types. Solver and
AutoFixLoop bridge events use existing `type=handoff` / `type=decision`
with `payload.kind=solver` (per spec edit E16).

MAGMA event surface: 51 unique event types (machine-derived; see
`tools/audit_v3_13_0_event_surface.py --count-only`). Was 48 at
Sprint 1 substrate landing; BC2 follow-up added `behavior.capture_refused`
and SolverProvenance M4 follow-up added `solver.run_result_recorded`;
AutoFixLoop AFL5 added `auto_fix_loop.lease_record_unparseable`.
Grouped:
* `write.*` (12): WriteRCOGate audit envelope
* `shadow.*` (8): ShadowRunner state + abort reasons
  (incl. `run_started`, `run_completed`, `baseline_failed`)
* `auto_fix_loop.*` (9): lease, cursor, repair lifecycle,
  including `lease_record_unparseable`
* `auth.*` (6): credential_stored, retrieved, rotated, revoked,
  material_revealed, and the ANTI-004 `credential_in_repo_blocked`
* `solver.*` (6): provenance_signed, run_result_recorded, quarantined,
  activation_authorised, activation_refused, activation_revoked
* `parser.*` (2): `unparsed_recorded` (no-silent-fail wrapper) and
  `silent_skip_lint` (ANTI-005 static lint finding)
* `behavior.*` (2): `behavior.captured`, `behavior.capture_refused`
  (BC2 follow-up: emitted before each CaptureRefused raise so
  refusal patterns are observable in MAGMA, not silent exceptions)
* `divergence.*` (1): `divergence.scored`
* `safety.*` (1): ANTI-001 `bulk_read_attempted`
* `schema.*` (1): ANTI-002 `text_date_sort_blocked`
* `sqlite.*` (1): ANTI-003 `parallel_writer_attempted`
* `api.*` (1): ANTI-006 `rate_limit_violated`
* `memory.*` (1): ANTI-007 `original_layer_modification_attempted`

Reproducible: `python tools/audit_v3_13_0_event_surface.py` prints the
full grouped list. The audit script is the single source of truth so
the docs cannot silently drift from the code.

Explicit non-deliverables (Sprint 2+):
* No new CLI entry points. `tools/sim_orchestrator.py` gained
  streaming Python API, not CLI flags.
* No real-data activation. All runs are dry-run with synthetic data.
* No DocIngest, SolverSynthesizer, SituationRoom.
* No cryptographic operator signatures (bridge-event mechanism only).
* No autonomous daemon harness for AutoFixLoop (`run_once(cursor)`
  is operator-driven).
* No Stage-2 cutover.

Stable-promotion gate for v3.13.0:
* Not active. v3.13.0 is substrate-only; stable promotion requires
  Sprint 2 activation + operator-signed solver activation + INST-G09
  on operator-real corpus. None of these are in scope for Sprint 1.

## Release-Ready Definition

`release-ready` means truthful readiness:

* green CI and targeted local validation at the release commit,
* reproducible proof commands,
* Docker status documented and, for stable, verified,
* security/privacy gates green,
* known limitations documented,
* no consciousness / AGI / raw model-superiority claim,
* release notes match the measured state.

## R21 Prerelease Gate

`v3.11.0-r20-axis-b-activated-alpha` passed the operator's R21.5
prerelease gate:

* R21.1 had a real `delta_quality` number.
* Part 1 baseline/synthesis/operator-decision files landed.
* R21.4 gate re-verification was green.
* R20 Decision B's five conditions were explicitly checked off.
* Profile S environment fix merged.

R21 is still a prerelease. It did not promote `v3.11.0` stable and did
not move Docker `:latest`.

## R22 / R23 Stable-Candidate Substrate

R22/R23 landed after the R21 prerelease and are part of the v3.12.0
stable-candidate surface:

* R22.0: redactor URL preservation, fail-closed redaction flag,
  Anthropic timeout/cost handling.
* R22.1a: production-shape solver-scale benchmark with HotPathCache
  attached.
* R22.2: hex-aligned oracle corpus; heuristic Axis B baseline lifted
  from 0.5 to 0.7619.
* R22.x: silent-bug fixes in BaselineStore locking, seasonal UTC month,
  and ControlPlaneDB rollback handling.
* R22.2d: 2D branch-isolation baseline benchmark.
* R23.0: wake-on-event bridge substrate.
* R23.1: heartbeat + automation gates.
* R23.2: dedicated per-agent worktree bootstrap.
* R23.1.1: orphan-job cleanup for watcher/heartbeat jobs.

## Current Measured Claims

Latest local claim audit (scratch run on 2026-05-10; measured numbers repeated here):

| Measurement | Result |
|---|---:|
| 10k synthetic solver descriptors | PASS |
| 10k capability hits | 1000/1000 |
| 10k FIFO fallback / miss | 0 / 0 |
| 10k warm lookup p99 | 0.0497 ms |
| 10k cold lookup p99 after cache attach | 28.7341 ms |
| 50k synthetic solver descriptors | PASS |
| 50k capability hits | 2000/2000 |
| 50k FIFO fallback / miss | 0 / 0 |
| 50k warm lookup p99 | 0.2198 ms |
| 50k cold lookup p99 after cache attach | 354.8117 ms |
| Mined solver dispatch | 21/21 cases PASS |
| Branch-isolation single-hot p99 degradation | 2.806x |
| Branch-isolation adversarial p99 degradation | 12.217x |
| Group-call pipeline targeted tests | 90 passed |

Interpretation:

* It is fair to say the warm capability-routing path is measured at
  10k and 50k synthetic descriptors.
* It is not fair to say all 50k lookup paths are low-latency.
* It is fair to say 2D branch isolation is measured.
* It is not fair to say branch isolation is solved.

## R22.5 Stable Promotion Gates

Before `v3.12.0` stable may be created:

* R22.1a, R22.2, and R22.3 must be merged or explicitly deferred by the
  operator.
* R22.5 soak window must complete: target 2026-05-10 -> 2026-05-24.
* Test-suite floor must be green at the tag commit.
* Profile S smoke must be green.
* Cloud/privacy findings at severity medium or higher must be closed or
  explicitly deferred.
* Axis A must show no unacceptable regression from current best numbers.
* Axis B gate must use the hex-aligned oracle baseline and any accepted
  Profile L treatment result.
* Docker stable workflow and `:latest` move policy must be finalized.
* Release notes must use the v3.12.0 template and include anti-claims.

Executable gate:

```text
python tools/check_release_gate.py \
  --release-readiness docs/release/RELEASE_READINESS.md \
  --soak-evidence docs/runs/release_soak_evidence/v3.12.0.json
```

The command is fail-closed. It must return `"decision": "pass"` before
a stable tag, GitHub release, GHCR `:stable`, or GHCR `:latest`
promotion is created. Until a real soak artifact exists, the expected
result is `"decision": "hold"`.

Optional evidence draft collection:

```text
python tools/collect_soak_evidence.py \
  --output docs/runs/release_soak_evidence/v3.12.0.json \
  --history docs/release/soak_evidence_history.jsonl
```

The collector is fail-closed: incomplete signals are written as
`"unknown"` or conservative defaults, and only `check_release_gate.py`
decides whether the evidence passes. Operators must supply explicit
pass evidence for CI, smoke, security/privacy, Axis A/B, Docker policy,
release-note anti-claims, silent failures, and clean logs before stable
promotion.

The soak evidence `commit` field is the audited evidence-subject commit:
the source tree whose CI, smoke, security/privacy, Axis A/B, Docker, and
release-note evidence was collected. It is not required to equal the
commit that merely stores the evidence file. This avoids an impossible
self-reference loop where a committed evidence file would need to contain
the SHA of the commit that includes that same file. If product/runtime
code changes after the evidence-subject commit, collect fresh evidence.
Evidence-only or documentation-only PRs do not reset the subject commit
when their own CI is green and the release gate still validates.

Required soak evidence schema:

```json
{
  "schema_version": "waggledance.release_soak.v1",
  "target_version": "v3.12.0",
  "commit": "<evidence-subject-commit-sha>",
  "started_at_utc": "2026-05-10T00:00:00Z",
  "ended_at_utc": "2026-05-24T00:00:00Z",
  "duration_hours": 336,
  "result": "pass",
  "silent_failures": 0,
  "error_log_clean": true,
  "ci_status": "pass",
  "profile_s_smoke": "pass",
  "security_privacy_gate": "pass",
  "axis_a_regression": "pass",
  "axis_b_gate": "pass",
  "docker_stable_policy": "finalized",
  "release_notes_anti_claims": "pass"
}
```

## Docker Readiness

Current stable Docker evidence is still the Phase 16F `v3.8.0` line:

* Docker Desktop 4.71.0 / Engine 29.4.1.
* `--network none`.
* Corpus 104.
* Four canonical proofs and autonomy_growth smoke suite passed.

R21 prerelease image exists on GHCR:

```text
ghcr.io/ahkeratmehilaiset/waggledance:v3.11.0-r20-axis-b-activated-alpha
```

R22/R23 stable-candidate Docker status:

* GHCR remains primary.
* Docker Hub is not configured.
* Dockerfile CMD, docker-compose command, and pyproject console script
  still need canonicalization before v3.12.0 stable.
* Docker `:latest` must not move until the operator authorizes stable
  promotion.

See `docs/deployment/DOCKER_QUICKSTART.md`.

## Documentation Readiness

Current state after R22.5 doc-surface cleanup:

| Surface | Status |
|---|---|
| `README.md` | Current landing text for R22/R23 state |
| `CHANGELOG.md` | Current sprint and release ledger |
| `docs/github/REPOSITORY_PRESENTATION.md` | Current GitHub/About truth boundary |
| `docs/deployment/DOCKER_QUICKSTART.md` | Current Docker truth boundary |
| `docs/release/RELEASE_READINESS.md` | This current release-gate summary |
| `CURRENT_STATE.md` | Regenerated in PR #212 |

`WAGGLEDANCE_AI_BRIEF.md` is not present on current `origin/main`; no
release-surface update is required for that file.

## Accepted lock exceptions

The v3.12.0 dependency lock carries exactly one documented exception
to the otherwise-stable-only pin policy. The exception is honest,
audit-traced, and time-bounded (tracked for upgrade to a final
stable when one becomes available).

* **`safetensors==0.8.0rc0`** — pre-release pin.
  * **Cause.** `diffusers==0.38.0` was bumped in PR #581 to clear
    four OSV vulnerabilities present in `diffusers==0.37.0`.
    `diffusers==0.38.0` declares the floor
    `safetensors>=0.8.0-rc.0`, so the previous `safetensors==0.7.0`
    no longer satisfies the resolver.
  * **Why not pin a later stable?** As of release-cut time, the only
    `safetensors` releases at or above `0.8.0` are `0.8.0.dev0` and
    `0.8.0rc0`. There is no stable `0.8.0` final on PyPI.
    `0.8.0rc0` is the least-bad satisfier (vs the `.dev0` build).
  * **Why not downgrade `diffusers`?** Downgrading reintroduces the
    four OSV vulnerabilities, which is strictly worse for the
    security gate.
  * **Vulnerability surface.** Both `safetensors==0.7.0` and
    `safetensors==0.8.0rc0` are OSV-clean (0 advisories).
  * **Tracking.** Upgrade to stable `safetensors>=0.8.0` final as
    soon as upstream cuts it; revisit at the next lock refresh.

This exception does NOT confer any operator-side allowance to add
more pre-release pins. New pre-release pins require a fresh
documented exception with the same four points (cause,
why-not-stable, why-not-downgrade, vulnerability surface) and an
explicit tracking note.

## Anti-Claims

* No consciousness, sentience, AGI, or human-like understanding claim.
* No raw-intelligence-superiority claim.
* No cross-vendor model ranking.
* No claim that the 50k cold path is low-latency.
* No claim that live agent clone capacity equals simultaneous live LLM
  completion capacity.
* No claim that 2D branch isolation is solved.
* No 3D topology or per-cell sharding in the current release.

## Historical References

* `CHANGELOG.md` - full release/sprint chronology.
* `docs/runs/phase16f_docker_stable_gate_2026_05_03/` - stable Docker
  evidence for v3.8.0.
* `iterations/codex_scout_tasks/r22_2d_branch_isolation_baseline_2026_05_10.md`
  - R22 2D branch-isolation measurement.
* Latest local 10k/50k solver and group-call claim audit was run on 2026-05-10; `.codex-audit/` is scratch state, not a public release artifact.
