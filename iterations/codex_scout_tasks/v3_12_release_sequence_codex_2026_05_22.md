# v3.12.0 Release Sequence Status - Codex 2026-05-22

Status: not release-ready. Stable promotion remains fail-closed.

Current target:

- Target version: v3.12.0 stable.
- Earliest allowed stable date: 2026-05-24.
- Latest stable remains: v3.8.0.
- Gate command:

```powershell
python tools/check_release_gate.py `
  --release-readiness docs/release/RELEASE_READINESS.md `
  --soak-evidence docs/runs/release_soak_evidence/v3.12.0.json
```

## 1. R22.5 Soak

Still blocked by time.

- Required window: 2026-05-10T00:00:00Z -> 2026-05-24T00:00:00Z.
- Required duration: 336h.
- Draft evidence generated at 2026-05-22T09:31:17Z had duration_hours=297.521.
- Release gate correctly reports hold with `before_no_earlier_than_date`,
  `soak_window_incomplete`, `soak_evidence_duration_lt_336h`, and
  `soak_evidence_ended_before_required_soak_end`.

## 2. Soak Evidence

Created fail-closed draft:

- `docs/runs/release_soak_evidence/v3.12.0.json`
- `result`: hold
- Conservative fields remain unknown/draft until final evidence exists.

Current gate blockers after the draft exists:

- before_no_earlier_than_date
- soak_window_incomplete
- soak_evidence_result_not_pass
- soak_evidence_duration_lt_336h
- soak_evidence_ended_before_required_soak_end
- soak_evidence_silent_failures_nonzero
- soak_evidence_error_log_not_clean
- soak_evidence_docker_policy_not_finalized
- soak_evidence_ci_status_not_pass
- soak_evidence_profile_s_smoke_not_pass
- soak_evidence_security_privacy_gate_not_pass
- soak_evidence_axis_a_regression_not_pass
- soak_evidence_axis_b_gate_not_pass
- soak_evidence_release_notes_anti_claims_not_pass

## 3. R22 Gates

Observed from local release docs and EVOLUTION_INDEX:

- R22.1a is recorded in release readiness as shipped production-shape
  HotPathCache benchmark realism.
- R22.2 is recorded as shipped hex-aligned oracle baseline with Axis B
  quality 0.7619.
- R22.3 remains the live decision point: Profile L Anthropic A/B requires
  operator API key, or the operator must explicitly defer it for v3.12.0.
- Per operator instruction, R22.3 was not run with stubs. Environment
  presence check only, without printing values:
  `ANTHROPIC_API_KEY=False`, `WAGGLE_BRIDGE_LLM_ENABLED=False`,
  `WAGGLE_FALLBACK_CHAIN=False`, `WAGGLE_BRIDGE_LLM_REDACTION=False`.
  Therefore a real Profile L treatment cannot be executed in this session.

Local preliminary checks:

```text
pytest tests/test_r22_hex_aligned_eval.py --basetemp=.pytest_tmp_r22_hex
=> 4 passed
```

## 4. Operator Decisions

Still required before stable:

- D1 PII-history scrub decision.
- Bootstrap kit sign-off:
  `configs/bootstrap_kit/GADGET.yaml` still has
  `signed_off_by: pending_operator_review_v3.12.0` and
  `signed_off_at_utc: pending`.
- Explicit Docker stable / `:latest` move decision.
- Explicit R22.3 path: run Profile L Anthropic A/B, or defer it.

## 5. Docker Canonicalization

Source-level entrypoint alignment is present:

- `Dockerfile` CMD: `python -m waggledance.adapters.cli.start_runtime`
- `docker-compose.yml` command: `python -m waggledance.adapters.cli.start_runtime`
- `pyproject.toml` script: `waggledance = waggledance.adapters.cli.start_runtime:main`

Still pending:

- final Docker stable workflow decision,
- GHCR stable/latest promotion policy finalization,
- final Docker verification evidence for v3.12.0 tag commit.

## 6. Release Worktree / Bridge Loop

Claude merged PR #556 for D4.1 hex-cell competition at 2026-05-22T09:25:18Z.

Claude opened PR #557 for the bridge-loop parity fixes:

- multi-recipient `to` parsing in `bridge_next_action.py`;
- `handoff/*_reported` closure parity;
- `agent_next_task.py` same-root events/claims inference from `--events`.

Codex local validation for PR #557:

```text
pytest tests/tools/test_bridge_next_action.py tests/tools/test_agent_next_task.py --basetemp=.pytest_tmp_pr557
=> 43 passed
git diff --check
=> pass, CRLF warnings only
```

Codex RCO approval was sent to Claude via bridge at
2026-05-22T09:30:53Z. The event's required fields are valid
(`type=decision`, `status=approved`, task_id matches PR #557 task);
the optional payload JSON was mangled by PowerShell quoting, but the
message contains the authoritative review result.

Follow-up parity gap found after the RCO:

- `bridge_next_action.py` did not treat `decision/status=approved` as an
  answer-like closure, so RCO handoffs could stay open after approval.
- Codex patch adds `approved` to `ANSWER_STATUS_FRAGMENTS`.
- Regression test added:
  `test_approved_decision_closes_review_requested_handoff`.
- Validation:

```text
pytest tests/tools/test_bridge_next_action.py tests/tools/test_agent_next_task.py --basetemp=.pytest_tmp_bridge_approved
=> 44 passed
git diff --check
=> pass, CRLF warnings only
```

Handoff sent to Claude:

- task_id: `bridge-approved-status-closure-2026-05-22`
- status: `review_requested`

Deterministic substrate smoke after bridge unblocked:

```text
pytest tests/unit/test_idle_consensus_charter.py -q --basetemp=.pytest_tmp_idle_consensus
=> 22 passed
```

## 7. Preliminary Local Proofs Run

Release gate / collector tests:

```text
pytest tests/test_release_gate_soak_evidence.py tests/tools/test_collect_soak_evidence.py --basetemp=.pytest_tmp_release_gate
=> 7 passed
```

Profile S preliminary smoke:

```text
python -c "import sys; from waggledance.core.bridge_llm import BridgeLLMClient, BridgeLLMRedactor; leaked=[n for n in ('anthropic','openai','ollama') if n in sys.modules]; assert not leaked, leaked; assert not BridgeLLMClient.disabled('profile_s').is_enabled(); assert BridgeLLMRedactor().redact('alice@example.org').applied; print('SMOKE_OK')"
=> SMOKE_OK
```

Profile S focused tests:

```text
pytest tests/test_solver_profile.py::test_profile_s_loads_with_no_llm_imports tests/test_bridge_llm_client.py::test_bridge_llm_client_module_imports_without_llm_libs tests/test_bridge_llm_client.py::test_client_disabled_short_circuits_to_heuristic tests/test_bridge_llm_client.py::test_default_client_honors_profile_env_disable tests/test_bridge_llm_redactor.py::test_importing_redactor_does_not_pull_in_anthropic_sdk --basetemp=.pytest_tmp_profile_s
=> 5 passed
```

## 8. Next Order

Recommended next actions, in order:

1. Wait for Claude to merge PR #557 if CI/preflight are green.
2. Review/shepherd `bridge-approved-status-closure-2026-05-22`.
3. Decide R22.3: operator API key run or explicit defer.
4. Keep collecting soak health until 2026-05-24T00:00:00Z or later.
5. Run full release floor at the intended tag commit.
6. Triage remaining Bandit MEDIUM findings or defer them explicitly.
7. Finalize Docker stable/latest policy and Docker verification evidence.
8. Operator signs GADGET bootstrap kit or defers it explicitly.
9. Operator decides D1 PII-history scrub.
10. Update `docs/runs/release_soak_evidence/v3.12.0.json` with final pass
   evidence only after every field is actually proven.
11. Re-run `tools/check_release_gate.py` without `--allow-hold`; only tag and
   promote if it returns `decision=pass`.

## 9. Security / Privacy Precheck

Preliminary cloud/privacy tests:

```text
pytest tests/test_bridge_llm_redactor.py tests/test_bridge_llm_client.py tests/unit_core/test_bridge_llm_adapter.py --basetemp=.pytest_tmp_privacy_gate
=> 74 passed
```

Bandit precheck:

- Initial local scan: HIGH=0, MEDIUM=32, LOW=237.
- Fixed B506 in `waggledance/core/reasoning/solver_router.py` by replacing
  `yaml.load(..., Loader=SafeLoader)` with `yaml.safe_load(...)`.
- Validation for the fix:

```text
pytest tests/unit_core/test_solver_signal_registry.py tests/autonomy/test_solver_router.py tests/autonomy_growth/test_solver_router_autonomy_consult.py --basetemp=.pytest_tmp_solver_router
=> 57 passed
```

- After B506 fix: HIGH=0, MEDIUM=31, LOW=237.
- Additional B310 hardening:
  - `waggledance/adapters/cli/start_runtime.py` validates the Ollama probe
    URL scheme/netloc before `urlopen`.
  - `core/auto_install.py` documents the constant HTTPS Voikko dictionary URL
    before suppressing B310.
- Runtime/packaging validation: 38 passed.
- Current Bandit after B506+B310 hardening: HIGH=0, MEDIUM=29, LOW=237.
- Additional static hardening completed after the no-stub test/simulation
  request:
  - B307 removed from `constraint_engine` and `math_solver` by using
    AST-whitelisted `safe_eval`.
  - B608 removed by quoting SQLite identifiers and documenting
    allowlisted/parameterized SQL fragments.
  - B104 documented as intentional Docker/production wildcard bind without
    changing runtime behavior.
  - B614 removed with `torch.load(..., weights_only=True)`.
  - B615 made fail-closed/local-first with `local_files_only=True` on
    Hugging Face loads and documented suppressions.
  - Current Bandit:
    `docs/runs/release_soak_evidence/v3.12.0_bandit_report_after_static_hardening_zero_medium.json`
    reports HIGH=0, MEDIUM=0, LOW=237 for `core waggledance`.
- Additional validation for the static hardening:

```text
pytest tests/test_safe_eval.py tests/unit_core/test_math_solver.py tests/test_no_regressions.py --basetemp=.pytest_tmp_b307_core
=> 51 passed
python tests/test_constraint_engine.py
=> 11/11 passed
pytest tests/autonomy/test_capability_adapters.py tests/unit_core/test_memory_engine_orchestration.py --basetemp=.pytest_tmp_b307_adapters
=> 25 passed
pytest tests/unit_core/test_storage_health.py tests/unit_core/test_retention_policy.py --basetemp=.pytest_tmp_b608_storage
=> 26 passed
pytest tests/unit/test_sqlite_shared_memory.py tests/unit/test_sqlite_trust_store.py --basetemp=.pytest_tmp_b608_memory_trust
=> 39 passed
pytest tests/providers/test_provider_plane.py tests/autonomy_growth/test_dispatch_by_features.py --basetemp=.pytest_tmp_b608_provider_cp
=> 15 passed
pytest tests/integration/test_runtime_cli.py tests/autonomy/test_runtime_cutover_config.py tests/test_ollama_probe.py --basetemp=.pytest_tmp_b104_runtime
=> 84 passed
pytest tests/unit_core/test_memory_engine_orchestration.py tests/test_pipeline.py --basetemp=.pytest_tmp_hf_local
=> 16 passed
pytest tests/test_b4_error_handling.py tests/autonomy/test_capability_adapters.py --basetemp=.pytest_tmp_translation_local
=> 18 passed
```

- Docker full-functionality check: `docker version` failed because `docker`
  is not installed / not on PATH in this environment. Docker release evidence
  remains blocked; no Docker stub was used.
- pip-audit escalated precheck wrote
  `docs/runs/release_soak_evidence/v3.12.0_pip_audit_report.json` and
  remains blocking: CLI reported 52 known vulnerabilities in 21 packages;
  parsed JSON has 21 dependency entries with vulnerabilities and 52 vuln
  entries.
- Direct CI/lower-bound dependency slice completed:
  - `aiohttp>=3.13.4` and `pytest>=9.0.3` in `requirements.txt`,
    `requirements-ci.txt`, and `pyproject.toml`;
  - escalated real install imported `aiohttp=3.13.5`, `pytest=9.0.3`;
  - validation 40 passed + 74 passed;
  - escalated pip-audit after the install reports 19 vulnerable dependency
    entries / 41 vulnerability entries in
    `docs/runs/release_soak_evidence/v3.12.0_pip_audit_report_after_direct_ci_deps.json`.
  - This does not pass the Docker/repro release dependency gate because
    `requirements.lock.txt` still needs a full refresh.
- Details:
  `docs/runs/release_soak_evidence/v3.12.0_security_privacy_precheck.md`
- Dependency upgrade/defer matrix:
  `docs/runs/release_soak_evidence/v3.12.0_pip_audit_upgrade_matrix.md`

`security_privacy_gate` remains non-pass because dependency vulnerabilities are
still real and must be updated or triaged/deferred. Bandit medium findings are
closed in local precheck.

## 10. No-Stub Local Artifact Collector Update

Implemented a release-evidence collector improvement in
`tools/collect_soak_evidence.py`:

- new `--use-local-artifacts` mode derives supported fields from actual local
  evidence artifacts rather than manual status stubs;
- currently derives:
  - `profile_s_smoke` from the privacy/Profile S precheck artifact;
  - `security_privacy_gate` from Bandit + pip-audit + privacy precheck;
  - `release_notes_anti_claims` from `docs/releases/v3.12.0.md`;
- manual `--status security_privacy_gate=pass` is overridden back to
  `blocked` if local audit evidence has dependency vulnerabilities or
  pip-audit skipped dependencies.

Validation:

```text
python -m py_compile tools/collect_soak_evidence.py
=> pass

pytest tests/tools/test_collect_soak_evidence.py tests/test_release_gate_soak_evidence.py --basetemp=.pytest_tmp_collect_local_artifacts2
=> 12 passed

pytest tests/test_bridge_llm_redactor.py tests/test_bridge_llm_client.py tests/unit_core/test_bridge_llm_adapter.py --basetemp=.pytest_tmp_privacy_gate_real
=> 74 passed

python -c "... Profile S smoke ..."
=> SMOKE_OK
```

Collector output with local artifacts:

```text
profile_s_smoke=pass
release_notes_anti_claims=pass
security_privacy_gate=blocked
result=hold
```

Release gate after collector update:

```text
today 2026-05-22: decision=hold
simulated 2026-05-24: decision=hold
```

Notable improvement: `release_notes_anti_claims` is no longer a blocker because
the candidate notes include the required anti-claim truth statements. Remaining
non-date blockers are real: dependency audit, CI evidence, Axis A/B, Docker
policy, silent failures, and clean log evidence.

## 11. Open Claude PR RCO Results

PR #558 (`r22-3-anthropic-status`) reviewed via GitHub and bridge:

- status: MERGED after round-2 RCO pass;
- blocker fixed: broad `Exception` narrowed to expected import/module
  availability error handling, so provider runtime errors fail loudly instead
  of being masked as `anthropic_status=unavailable`;
- local isolated validation before merge:
  `tests/test_r21_oracle_ab_proof.py` +
  `tests/test_r22_hex_aligned_eval.py` => 18 passed;
- squash commit:
  `d7a7a3df2b7fd54e3bfcb24e16eca7522094492b`.

PR #559 (`d1-pii-scrub-tool`) reviewed via GitHub and bridge:

- status: MERGED after round-2 RCO pass;
- blocker fixed: detect now emits tri-state `decision`; `unverifiable` keeps
  `scrub_needed=true`, so redacted HEAD without known values can no longer
  look like a clean false;
- local isolated validation before merge:
  `tests/tools/test_d1_pii_scrub.py` => 16 passed, 2 skipped;
- GitHub checks before merge:
  security-scan, test 3.11, test 3.12, test 3.13, unified all passed;
- squash commit:
  `041f41c0ef2fcf21a471566a722e0417f1689f47`.

PR #560 (`flaky-offset-scaling`) reviewed via GitHub and bridge:

- status: MERGED after RCO pass;
- scope: test-only de-flake for vector event offset scaling guard;
- local isolated validation before merge:
  `tests/test_vector_events.py` => 32 passed;
- squash commit:
  `8463e923f6c47692c0f6b10cde191c75e00162c0`.

All three open Claude RCO handoffs from this release slice are now closed in
bridge with merge events.

## 12. Dependency Lock and Torch Blocker

Claude RCO correctly blocked bundling the dependency-lock refresh with this
security-hardening PR because the tested auditable Torch path resolved to CPU
wheels:

```text
torch=2.11.0+cpu
torchaudio=2.11.0+cpu
torchvision=0.26.0+cpu
cuda_available=False
```

That would remove the existing Windows `+cu118` CUDA build from the shipped
lock. The tradeoff is real and belongs in a separate operator-gated PR, not in
the static hardening slice.

Current PR boundary after the split:

- `requirements.lock.txt` is back to `origin/main`.
- Torch dry-run/audit artifacts and the dependency upgrade matrix are removed
  from this PR.
- Static Bandit hardening, no-stub local artifact derivation, Profile S/privacy
  smoke evidence, and release HOLD evidence remain in scope.
- `security_privacy_gate` is blocked, not pass, until the dependency-lock path
  is resolved with explicit CUDA/GPU tradeoff approval or a CUDA-preserving
  audited alternative.

Collector and gate after the split:

```text
profile_s_smoke=pass
release_notes_anti_claims=pass
security_privacy_gate=blocked
result=hold
```

Release remains HOLD for dependency audit, CI evidence, Axis A/B evidence,
Docker policy, silent failures, clean log evidence, and final soak timing.
