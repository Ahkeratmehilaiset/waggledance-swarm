# Phase 18D — P0 Baseline Verification

**Date (UTC):** 2026-05-06
**Operator worktree:** `C:\Python\project2-master`
**origin URL:** `https://github.com/Ahkeratmehilaiset/waggledance-swarm.git`

## origin/main

`1a51dcdbd51abfc3e64311bc20ea4eab2ebd987d` — Phase 18C post-release docs PR #84 squash-merge (2026-05-05T19:01:17Z).

## Tag SHAs (8 prior tags — must be unchanged through Phase 18D)

| Tag | Commit SHA | Status |
| --- | --- | --- |
| `v3.8.0` | `824176ebf2a6b8debed41982090a125cbe2ddad1` | GitHub **Latest**, isPrerelease=false |
| `v3.9.0-producer-fabric-alpha` | `c726995c816ee4c09e031c2190c3de6592e82879` | Pre-release |
| `v3.9.1-local-efficiency-benchmark-alpha` | `f4d0a4a4152ca74e98a8d7f7161c233075bf4111` | Pre-release |
| `v3.9.2-local-ollama-baseline-alpha` | `db5d7db1ecb9ae6f17293f0bf7261f4c9d40e91c` | Pre-release |
| `v3.9.3-local-model-sweep-alpha` | `d0704efe46be18d480ed425ff83b087cd36ef9bd` | Pre-release |
| `v3.10.0-benchmark-schema-alpha` | `4554b24a47045ab10c1c0fbcb010f695d47d867c` | Pre-release |
| `v3.10.1-gap-miner-feedback-alpha` | `b408b14a4209ee9f8da00f040223a988815d0f87` | Pre-release |
| `v3.10.2-mined-solver-dispatch-alpha` | `e9aa1de10376109987a7c18d331bbcc996a9ddf9` | Pre-release |

## GitHub release ordering (gh release list, top 8)

```
v3.10.2-mined-solver-dispatch-alpha — Phase 18C   Pre-release   2026-05-05T18:44:19Z
v3.10.1-gap-miner-feedback-alpha — Phase 18B      Pre-release   2026-05-05T14:32:46Z
v3.10.0-benchmark-schema-alpha — Phase 18A        Pre-release   2026-05-05T07:20:31Z
v3.9.3-local-model-sweep-alpha — Phase 17D        Pre-release   2026-05-05T06:05:30Z
v3.9.2-local-ollama-baseline-alpha — Phase 17C    Pre-release   2026-05-04T22:26:28Z
v3.9.1-local-efficiency-benchmark-alpha — Phase 17B Pre-release 2026-05-04T20:59:09Z
v3.9.0-producer-fabric-alpha — Phase 17A          Pre-release   2026-05-04T18:32:47Z
v3.8.0 — stable release                           Latest        2026-05-04T07:13:27Z
```

`v3.8.0` confirmed as `Latest`, all 7 alphas above it confirmed `Pre-release`.

## Verdict

Baseline GREEN: origin/main is consistent with the post-Phase-18C state, all 8 prior tags resolve to expected SHAs, GitHub release metadata matches expectation. Phase 18D may proceed to inventory.
