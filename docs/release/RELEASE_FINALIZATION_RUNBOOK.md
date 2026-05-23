# v3.12.0 Release Finalization Runbook

This runbook is the exact mechanical sequence to flip
`docs/runs/release_soak_evidence/v3.12.0.json` from `result=hold` to
`result=pass` once the soak window completes, and to verify the release
gate accepts it. It is the only finalization recipe; do not improvise
status fields by hand.

## Pre-conditions to verify

Before running the re-collect, confirm all four:

1. **Today is on or after the soak end.** Per
   `docs/release/RELEASE_READINESS.md` the soak window is
   `2026-05-10 → 2026-05-24`. The gate refuses any `--today` before
   `2026-05-24` regardless of evidence content.
2. **`origin/main` head has green CI.** Use the latest merged commit
   relevant to the audited surface (this is the
   *evidence-subject* commit per
   `docs/release/RELEASE_READINESS.md` §"soak evidence subject commit"
   semantics — not necessarily the commit that will store the new
   evidence file).
3. **Current evidence file already shows every status field at the
   expected non-hold value.** Inspect
   `docs/runs/release_soak_evidence/v3.12.0.json` and verify:
   - `axis_a_regression`, `axis_b_gate`, `ci_status`,
     `profile_s_smoke`, `release_notes_anti_claims`,
     `security_privacy_gate` ⇒ all `"pass"`.
   - `docker_stable_policy` ⇒ `"finalized"`.
   - `silent_failures` ⇒ `0`. `error_log_clean` ⇒ `true`.
   - `started_at_utc` ⇒ `"2026-05-10T00:00:00Z"`. `target_version` ⇒
     `"v3.12.0"`. `schema_version` ⇒ `"waggledance.release_soak.v1"`.
   - Only `result` (currently `"hold"`), `ended_at_utc`, `commit`, and
     `duration_hours` should change at finalization.
4. **Operator decision packs are signed.** Verify
   `docs/operator_inbox/torch-cuda-vs-cpu.yaml` and
   `docs/operator_inbox/docker-latest-promotion.yaml` both have a
   non-empty `operator_signoff.signed_by` and `chosen_option`. The
   docker pack must specify `chosen_option: ghcr_stable_only`
   (latest tag does NOT move at v3.12.0 stable).

If any pre-condition fails, STOP. Treat the failure as a real finding;
do not weaken the gate to ship.

## Step 1 — Re-collect evidence (preferred: `--use-local-artifacts`)

Run from the repository root, on a branch off `origin/main`. Replace
`<subject-sha>` with the evidence-subject commit identified in
pre-condition 2 (40-character SHA).

```bash
python tools/collect_soak_evidence.py \
  --release-readiness docs/release/RELEASE_READINESS.md \
  --commit <subject-sha> \
  --ended-at-utc 2026-05-24T00:00:00Z \
  --use-local-artifacts \
  --output docs/runs/release_soak_evidence/v3.12.0.json \
  --history docs/runs/release_soak_evidence/v3.12.0_history.jsonl
```

`--use-local-artifacts` derives the six status fields, `silent_failures`,
`error_log_clean`, and `docker_stable_policy` from the canonical artifact
files already in `docs/runs/release_soak_evidence/` (e.g.
`v3.12.0_ci_status.json`, `v3.12.0_axis_a_solver_scale*`,
`v3.12.0_axis_b_hex_aligned_eval.json`, `v3.12.0_docker_policy.json`,
`v3.12.0_soak_log_audit.json`). This is the fail-closed default: if any
underlying artifact is stale, missing, or non-pass, the corresponding
status field will be `unknown` or non-pass and the gate will refuse.
Do NOT layer manual `--status` overrides on top to "fix" an unknown.

If `--use-local-artifacts` reports a per-field mismatch versus the
current `v3.12.0.json`, that is a real signal — investigate the
underlying artifact, not the status flag.

## Step 2 — Verify the gate accepts the new evidence

```bash
python tools/check_release_gate.py \
  --release-readiness docs/release/RELEASE_READINESS.md \
  --soak-evidence docs/runs/release_soak_evidence/v3.12.0.json \
  --today 2026-05-24
```

Expected output exactly:

```json
{
  "blockers": [],
  "decision": "pass",
  "latest_stable": "v3.8.0",
  "no_earlier_than": "2026-05-24",
  "soak_window": {
    "end": "2026-05-24",
    "required_hours": 336,
    "start": "2026-05-10"
  },
  "target_version": "v3.12.0"
}
```

`decision != "pass"` ⇒ STOP. Read the `blockers` array; each entry is
a fail-closed gate clause from `tools/check_release_gate.py`. Common
ones and what they mean:

- `before_no_earlier_than_date` ⇒ system clock is wrong, or it is not
  yet 2026-05-24 UTC.
- `soak_evidence_duration_lt_336h` ⇒ `ended_at_utc - started_at_utc`
  is below 336 hours; usually means `--ended-at-utc` was supplied
  earlier than `2026-05-24T00:00:00Z`.
- `soak_evidence_ended_before_required_soak_end` ⇒ same root cause
  expressed as a different invariant.
- `soak_evidence_<field>_not_pass` ⇒ a status field in the JSON is not
  the expected pass value; do NOT hand-edit the JSON; re-run
  `collect_soak_evidence` after fixing the underlying artifact.
- `soak_evidence_result_not_pass` ⇒ the collector did not derive
  `result=pass`; the most common cause is a missing
  `--use-local-artifacts` flag or stale artifact. Re-collect.

## Step 3 — Land the evidence update via PR

The new `v3.12.0.json` lands via a PR (Rule 6, PR-only — no direct
push to `main`). PR scope: only the two evidence files (`v3.12.0.json`,
`v3.12.0_history.jsonl`). RCO by the peer agent before merge. The
PR's own CI must be green; the head must match at merge
(`gh pr merge --squash --match-head-commit=<head>`). Per
`#587` semantics, the PR's storing commit is allowed to differ from
the evidence-subject commit recorded in `commit`; no
self-reference loop.

## Step 4 — Operator-only finalization

These steps are operator-only (Rule 10 atomic-flip discipline) and
encoded in the signed decision packs. They are listed here for
completeness; an agent must NOT execute them autonomously.

1. **Tag** (after the evidence PR merges and gate verification
   returns `decision: pass` on the merged `main`):
   ```bash
   git tag -s v3.12.0 -m "v3.12.0 stable"
   git push origin v3.12.0
   ```
2. **Docker promotion** per `docs/operator_inbox/docker-latest-promotion.yaml`:
   - `chosen_option: ghcr_stable_only` ⇒ push
     `ghcr.io/ahkeratmehilaiset/waggledance:stable` and
     `ghcr.io/ahkeratmehilaiset/waggledance:v3.12.0`.
   - **DO NOT** move `ghcr.io/.../waggledance:latest`
     (`latest_move: false` in the signed pack; `:latest` stays on
     `v3.8.0`).
   - Docker Hub is not configured for this release.
3. **Release announcement** is operator-owned and follows the
   evidence PR + tag, not before.

## Anti-claims for this runbook

- An agent MUST NOT execute Step 4 autonomously. Tag creation and
  Docker promotion are operator-only.
- An agent MUST NOT hand-edit status fields in `v3.12.0.json` to make
  the gate pass. If a status field is wrong, the underlying artifact
  is wrong; fix the artifact and re-collect.
- An agent MUST NOT supply `--today` later than the actual UTC date
  to coerce the gate. The gate's time clauses exist exactly to
  prevent that bypass.
- The runbook does NOT cover hotfix releases or rollbacks; those have
  their own (yet-unwritten) procedures.

## References

- `tools/collect_soak_evidence.py` — evidence collector (writer).
- `tools/check_release_gate.py` — fail-closed gate (reader).
- `docs/release/RELEASE_READINESS.md` — release-window definition and
  accepted lock exceptions.
- `docs/operator_inbox/torch-cuda-vs-cpu.yaml`,
  `docs/operator_inbox/docker-latest-promotion.yaml` — signed
  operator decision packs gating this release.
- `docs/architecture/STAGE2_CUTOVER_RFC.md` — soak-log audit
  invariant (G3) feeding `v3.12.0_soak_log_audit.json`.
- `tests/tools/test_release_gate_soak_evidence.py` — regression
  guards on the gate semantics; updated in PR #587 to lock the
  evidence-subject-commit rule.
