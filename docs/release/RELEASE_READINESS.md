# Release Readiness - WaggleDance

This file summarizes the current release state. Historical per-phase
details live in `CHANGELOG.md` and `docs/runs/*`.

## Current Status

* **Latest stable release**: `v3.8.0` (Phase 16F, released 2026-05-04).
* **Latest R21 prerelease**:
  `v3.11.0-r20-axis-b-activated-alpha` (released 2026-05-10).
* **Current mainline posture**: R22/R23 stable-candidate substrate on top
  of the R21 alpha, no package-version bump.
* **Next stable target**: `v3.12.0`, no earlier than 2026-05-24, after
  the R22.5 soak and promotion gates.
* **Docker registry**: GHCR primary. No Docker Hub mirror yet.
* **GitHub Latest**: remains `v3.8.0` until the stable promotion
  explicitly moves it.

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
| Mined solver dispatch | 18/18 cases PASS |
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
