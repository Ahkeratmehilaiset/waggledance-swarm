# EIG2 M1 Initial Checkpoint

Date: 2026-05-11.
Checkpoint type: M1.0 prep.
Base commit: `origin/main` `2cc6fec28d4103b8c7deab6aad8488866b5e3ba6`.

## Accepted ADRs

M0 ADR set accepted and merged:

- ADR-010 M0 preflight before runtime work.
- ADR-011 compact-card write-storm breaker.
- ADR-012 M0 scope freeze.
- ADR-013 RCO inventory required before adapter proposals.
- ADR-014 queue and backpressure before breaker for EIG2 writes.
- ADR-015 Option-B compliance test per new EIG2 writer.
- ADR-016 atomic flip via Stage-2 RFC.
- ADR-017 `.eig2.autonomous_merge` label-only semantics.
- ADR-018 version monotonicity.
- ADR-019 no-human policy scope.
- ADR-020 bridge `type` field non-gating.

M0 PRs merged:

- PR #263: M0 reality-check inventory and 200-option summary, merge commit `10d849314498efccf1961cbf5a25b24476bb0397`.
- PR #265: bridge polymorphic reply classifier on main, merge commit `0133cb16f90e202f35acb60b6e01d931282fc425`.
- PR #266: ADR 020 initial landing, merge commit `ccd4d12d4eea1fc048a56220162374107f0a756f`.
- PR #267: ADR 020 review amendments, merge commit `b10a95a458738f5a0c90594a3c54b2f82c712350`.
- PR #268: M0 orchestrator shims, merge commit `ba27ae13fe10038d18dd5ffbc5b36108800eb658`.
- PR #269: M0 ADR set, merge commit `2cc6fec28d4103b8c7deab6aad8488866b5e3ba6`.

Post-merge sanity on `2cc6fec...` passed before this checkpoint:

- `pytest --basetemp=.pytest_tmp tests/orchestrator tests/contracts -q` -> 50 passed.
- `no_human_prompt_lint.py` over M0 ADR/orchestrator/config surfaces -> 0 findings.
- `git diff --check HEAD` -> passed, with only the known global git ignore permission warning.

## Active locks and claims

Bridge task: `eig2-m1-architecture-map-2026-05-11`.

Active Codex write scope:

- `docs/architecture/explosive_intelligence_growth_2.md`
- `.orchestrator/autonomous_merge_snapshot.json`
- `.orchestrator/checkpoints/M1-initial.md`
- `docs/eig2/adr/000-eig2-m0-index.md`

Claude RCO review was requested through bridge thread `eig2-m1-kickoff-2026-05-11`.
Claude acknowledged the M1.0 docs/orchestrator-only approach and requested the
M0 completion marker plus the post-M0 hardening backlog enumeration.

## Modified files in this PR

- `docs/architecture/explosive_intelligence_growth_2.md`
- `.orchestrator/autonomous_merge_snapshot.json`
- `.orchestrator/checkpoints/M1-initial.md`
- `docs/eig2/adr/000-eig2-m0-index.md`

No `waggledance/core/*` files are modified in M1.0.

## Current feature flags

From `configs/explosive_intelligence_growth_v2.yaml` at base commit:

- `enabled: false`
- `implemented: true`
- `production_default: "hex2d_sparse_tunnels"`
- `enable_requires_profile_or_test_flag: true`
- `topology.virtual_3d_enabled: false`
- `topology.virtual_4d_enabled: false`
- `topology.benchmark_only: true`
- `tunnels.learned_candidates_shadow_only: true`
- `resource_quotas.max_tunnel_promotions_per_day: 0`
- `autonomous_mode.autonomous_merge_to_main: false`

Startup snapshot:

- `.eig2.autonomous_merge` absent at startup.
- `.orchestrator/autonomous_merge_snapshot.json` records `enabled: false` and
  `must_ignore_later_changes: true`.

## Current invariants

- MAGMA raw append remains authoritative.
- Compact cards are optional derived summaries and must tolerate absence.
- New EIG2 writers require queue/backpressure before breakers.
- New EIG2 writers require ADR-015 Option-B compliance tests.
- Bridge continuity must not filter by `type == "message"`.
- LLM/provider calls are forbidden in hot routing and tunnel scoring.
- Virtual 3D/4D topology providers are benchmark-only.
- `.eig2.halt` and the self-modification denylist remain external safety
  controls for runtime behavior.
- Version names must be monotonic with published tags and release history.

## Open backlog inherited from post-M0 RCO audits

- B7: autogrowth scheduler claim leak on unexpected grower exception.
- B9: low-confidence gap recording does synchronous DB write on async chat path.
- B18: hybrid collection count fallback can create state from a GET read path.
- B19: hologram secret redactor over-redacts benign metric fields.
- B26: prompt builder language switching lock does not cover the full critical section.

These are not M1.0 blockers because this PR is documentation/orchestrator only.
They become higher priority before M3/M4 load-bearing runtime work.

## Validation commands for this PR

Completed before PR:

- `& C:\Python\project2-master\.python\Python313\python.exe -m json.tool .orchestrator\autonomous_merge_snapshot.json` -> passed.
- `git diff --check` -> passed, with only the known global git ignore permission warning and line-ending notice for the edited ADR index.
- `& C:\Python\project2-master\.python\Python313\python.exe .orchestrator\no_human_prompt_lint.py docs\architecture\explosive_intelligence_growth_2.md docs\eig2\adr .orchestrator configs\explosive_intelligence_growth_v2.yaml configs\eig2_self_modification_denylist.yaml` -> 0 findings.
- `& C:\Python\project2-master\.python\Python313\python.exe -m pytest --basetemp=.pytest_tmp tests\orchestrator tests\contracts -q` -> 50 passed.

## Unresolved risks

- The first runtime topology-provider PR must decide how to expose the 7-cell
  agent mesh and 8-cell solver topology without presenting them as one graph.
- The post-M0 hardening backlog should be fixed before EIG2 depends on the
  affected paths for sustained background or request-path load.
- The architecture map is descriptive. Runtime authority comes from ADRs,
  tests, config, and the later PR diffs.
