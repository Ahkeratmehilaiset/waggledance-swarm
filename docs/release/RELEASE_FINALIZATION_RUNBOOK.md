# v3.12.0 Release Finalization Runbook

This runbook is the exact mechanical sequence to reproduce the checked
`result=pass` in `docs/runs/release_soak_evidence/v3.12.0.json` from
current local artifacts, revalidate its exact retained subject provenance,
and verify the release gate accepts it. It is the only finalization recipe;
do not improvise status fields by hand.

## Pre-conditions to verify

Before running the re-collect, confirm all four:

1. **The actual UTC instant is on or after the soak end.** Per
   `docs/release/RELEASE_READINESS.md` the soak window is
   `2026-05-10T00:00:00Z → 2026-05-24T00:00:00Z`. The gate refuses
   evidence whose exact end timestamp is later than the checked UTC
   instant, including a later time on the same calendar day.
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
   - A provenance-only re-collection must retain the audited `commit`,
     `started_at_utc`, `ended_at_utc`, and `duration_hours` exactly.
     Changing the evidence-subject commit or soak window requires a complete
     fresh 336-hour soak and regeneration of every subject-bound artifact.
     `result` and every status field must remain derived from the canonical
     artifacts; never change them by hand.
4. **Operator decision packs are signed and machine-scoped.** Verify
   `docs/operator_inbox/torch-cuda-vs-cpu.yaml` has a non-empty
   `operator_signoff.signed_by` and `chosen_option`. The fresh Docker pack
   must be retained at
   `docs/operator_inbox/docker-v3-12-0-stable-promotion.yaml`, use
   `decision_id: docker-v3-12-0-stable-promotion`, and have all of:
   - `target_version: v3.12.0`;
   - `commit: <subject-sha>` matching pre-condition 2 exactly;
   - a `created_utc` timestamp;
   - a non-empty `operator_signoff.signed_by`;
   - `chosen_option: ghcr_stable_only` (latest does NOT move).

   The checked historical
   `docs/operator_inbox/docker-latest-promotion.yaml` predates these
   machine-scope requirements. Its human-readable text remains a signed
   historical v3.12.0 decision, but it is not valid input to the hardened
   Docker-policy evidence converter. Do not add scope fields underneath the
   old signoff. A fresh, already-scoped pack and fresh operator signoff are
   required. The fresh signoff timestamp must not predate the evidence-subject
   commit or the pack's creation timestamp.

If any pre-condition fails, STOP. Treat the failure as a real finding;
do not weaken the gate to ship.

## Step 1 — Generate exact-source Docker policy evidence

First retain the fresh scoped and signed pack in the evidence PR branch; the
generator rejects an untracked or locally modified authority source. Then,
from a clean checkout whose required Docker policy sources match
`<subject-sha>`, generate the canonical Docker artifact:

```bash
python tools/run_release_docker_policy_evidence.py \
  --source-root . \
  --commit <subject-sha> \
  --target-version v3.12.0 \
  --operator-decision-pack \
    docs/operator_inbox/docker-v3-12-0-stable-promotion.yaml \
  --output docs/runs/release_soak_evidence/v3.12.0_docker_policy.json
```

The command fails closed unless `<subject-sha>` exists in this repository and
is an ancestor of the current storage `HEAD`. Every required regular
working-tree file and index entry must match that commit after explicit
CRLF-to-LF normalization. Local Git environment overrides, replace objects,
attributes, and clean filters are not trusted. The retained decision-pack
index/worktree bytes must match its immutable `HEAD` blob, and duplicate YAML
keys are rejected. A stale, missing, malformed, or draft Docker artifact
remains `draft`; the soak collector does not reconstruct one silently from an
operator pack. Raw `--operator-authorization` JSON is not accepted:
authorization is re-derived from the retained pack and its recorded digest on
every evaluation. A chosen option's recognized machine fields must agree with
the policy derived from its ID with exact scalar types, and duplicate YAML
keys are rejected. The Dockerfile bytes are pinned to the reviewed release
policy. Its final stage must explicitly clear an inherited entrypoint with
`ENTRYPOINT []` and use the exact JSON-form canonical `CMD`; comments,
continued `RUN` lines, and heredocs cannot manufacture a passing command.

## Step 2 — Re-collect evidence (preferred: `--use-local-artifacts`)

Run from the repository root, on a branch off `origin/main`. Replace
`<subject-sha>` with the evidence-subject commit identified in
pre-condition 2 (canonical lowercase 40- or 64-character SHA).

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
`v3.12.0_soak_log_audit.json`). This explicit local-artifact mode is
fail-closed: if any underlying artifact is stale, missing, or non-pass, the
corresponding status field will be `unknown` or non-pass and the gate will
refuse. The retained soak-log audit must cover the entire claimed interval:
its start must be no later than `started_at_utc`, and its end must be no
earlier than `ended_at_utc`.

The collector records `collection_mode: local_artifacts` only for this
artifact-derived path. Manual/default collection records
`collection_mode: manual` and always derives `result: hold`, even if every
manual status flag says pass. Manual flags may describe a diagnostic draft;
they can never manufacture release-pass evidence. Do NOT layer manual
`--status` overrides on top to "fix" an unknown.

The mode string is not trusted by itself. Both the public gate and the
boundary reader independently re-evaluate the canonical local artifacts for
the recorded commit and soak interval, then require every derived status,
error count, and Docker-policy value to match the evidence exactly. Hand
editing `collection_mode` or `result` therefore remains HOLD.

The collector returns exit code `0` after successfully writing an artifact
even when its derived `result` is `hold`; malformed or unwritable input
returns `2`. Inspect `result`, then run the gate in Step 3. A successful
collector process is not a release-pass decision.

If `--use-local-artifacts` reports a per-field mismatch versus the
current `v3.12.0.json`, that is a real signal — investigate the
underlying artifact, not the status flag.

## Step 3 — Verify the gate accepts the new evidence

```bash
python tools/check_release_gate.py \
  --source-root . \
  --release-readiness docs/release/RELEASE_READINESS.md \
  --soak-evidence docs/runs/release_soak_evidence/v3.12.0.json \
  --checked-at-utc 2026-05-24T00:00:00Z
```

The key pass fields must include the following values (the command also emits
the full `soak_evidence_diagnostics` object):

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
- `soak_evidence_duration_lt_336h` ⇒ the finite reported duration is below
  336 hours.
- `soak_evidence_elapsed_duration_lt_336h` or
  `soak_evidence_duration_mismatch` ⇒ the timestamps do not prove the
  reported soak duration; regenerate the evidence instead of editing the
  duration field.
- `soak_evidence_ended_before_required_soak_end` ⇒ same root cause
  expressed as a different invariant.
- `soak_evidence_<field>_not_pass` ⇒ a status field in the JSON is not
  the expected pass value; do NOT hand-edit the JSON; re-run
  `collect_soak_evidence` after fixing the underlying artifact.
- `soak_evidence_result_not_pass` ⇒ the collector did not derive
  `result=pass`; the most common cause is a missing
  `--use-local-artifacts` flag or stale artifact. Re-collect.
- `soak_evidence_collection_mode_invalid` ⇒ the evidence was not derived
  from canonical local artifacts. Manual evidence remains HOLD.
- `soak_evidence_ended_in_future` ⇒ the evidence ends after the exact
  checked UTC instant, even if both timestamps share a calendar date.
- `checked_at_utc_in_future` ⇒ the supplied evaluation override is later
  than the real current UTC instant; a future override cannot coerce a pass.

After the gate passes, record the still-read-only boundary posture from the
same hardened Docker-policy artifact:

```bash
python tools/run_release_boundary_readiness.py \
  --source-root . \
  --docker-decision-pack \
    docs/operator_inbox/docker-v3-12-0-stable-promotion.yaml \
  --docker-policy-evidence \
    docs/runs/release_soak_evidence/v3.12.0_docker_policy.json \
  --soak-evidence \
    docs/runs/release_soak_evidence/v3.12.0.json \
  --output .codex-audit/release_boundary_readiness.json \
  --json \
  --strict
```

The boundary reader does not reinterpret a standalone Docker YAML pack. It
requires the v2 policy evaluator to revalidate the exact subject commit,
retained authority blob, derived blockers, and final policy state. Any
standalone, untracked, stale, or cross-commit pack keeps the boundary on hold.
The phase-synthesis refresh, read-only release-gate recheck, and Torch
decision pack must likewise come from their canonical repository paths and
match the regular-file `HEAD` blob, index entry, and working-tree bytes.
Their exact schemas, blocker types, and nested/top-level gate decision must
agree; schema-less or type-confused reports fail closed. A Torch signoff must
also be at or after the pack creation time and no later than the checked UTC
instant.
The Docker-policy commit must also equal the canonical soak-evidence commit.
`--strict` returns exit code `2` when readiness blockers remain; that means
STOP and inspect the printed JSON and its `release_boundary_blockers`. A valid
preflight exits `0` with `ready_for_operator_finalization`, but still keeps
every release-boundary effect false and performs no release action. The
explicit scratch output keeps this read-only preflight from overwriting the
tracked boundary record.

## Step 4 — Land the evidence update via PR

The new evidence lands via a PR (Rule 6, PR-only — no direct push to
`main`). Keep the PR scoped to `v3.12.0.json`,
`v3.12.0_history.jsonl`, the regenerated
`v3.12.0_docker_policy.json`, and the fresh scoped Docker decision pack.
Preserve the historical unscoped pack instead of overwriting its recorded
signoff. RCO by the peer agent before merge. The PR's own CI must be green;
the head must match at merge
(`gh pr merge --squash --match-head-commit=<head>`). Per
`#587` semantics, the PR's storing commit is allowed to differ from
the evidence-subject commit recorded in `commit`; no self-reference loop.
The storing commit must nevertheless descend from the evidence-subject
commit. An unrelated history with identical file bytes is rejected.

## Step 5 — Operator-only finalization

These steps are operator-only (Rule 10 atomic-flip discipline) and
encoded in the signed decision packs. They are listed here for
completeness; an agent must NOT execute them autonomously.

1. **Tag** (after the evidence PR merges and gate verification
   returns `decision: pass` on the merged `main`):
   ```bash
   git tag -s v3.12.0 -m "v3.12.0 stable"
   git push origin v3.12.0
   ```
2. **Docker promotion** per the fresh exact-scoped Docker decision pack used
   to generate `v3.12.0_docker_policy.json`:
   - `chosen_option: ghcr_stable_only` ⇒ push
     `ghcr.io/ahkeratmehilaiset/waggledance:stable` and
     `ghcr.io/ahkeratmehilaiset/waggledance:v3.12.0`.
   - **DO NOT** move `ghcr.io/.../waggledance:latest`
     (`ghcr_stable_only` structurally maps to `move_latest: no`;
     `:latest` stays on `v3.8.0`).
   - Docker Hub is not configured for this release.
3. **Release announcement** is operator-owned and follows the
   evidence PR + tag, not before.

## Anti-claims for this runbook

- An agent MUST NOT execute Step 5 autonomously. Tag creation and
  Docker promotion are operator-only.
- An agent MUST NOT hand-edit status fields in `v3.12.0.json` to make
  the gate pass. If a status field is wrong, the underlying artifact
  is wrong; fix the artifact and re-collect.
- An agent MUST NOT add `target_version` or `commit` beneath an existing
  operator signoff. Machine scope must be present before the operator signs.
- An agent MUST NOT supply `--checked-at-utc` later than the actual UTC
  instant to coerce the gate. The gate's exact-time clauses exist exactly to
  prevent that bypass.
- The runbook does NOT cover hotfix releases or rollbacks; those have
  their own (yet-unwritten) procedures.

## References

- `tools/collect_soak_evidence.py` — evidence collector (writer).
- `tools/run_release_docker_policy_evidence.py` — exact-scope,
  exact-source Docker policy artifact generator.
- `tools/check_release_gate.py` — fail-closed gate (reader).
- `docs/release/RELEASE_READINESS.md` — release-window definition and
  accepted lock exceptions.
- `docs/operator_inbox/torch-cuda-vs-cpu.yaml` — signed operator
  decision pack gating this release.
- `docs/operator_inbox/docker-v3-12-0-stable-promotion.yaml` — required
  future exact-scoped Docker authority source; it does not exist until the
  operator signs and the evidence PR retains it.
- `docs/operator_inbox/docker-latest-promotion.yaml` — historical signed
  Docker decision context only; its missing machine scope means it is not
  valid authorization for hardened Docker-policy evidence.
- `docs/architecture/STAGE2_CUTOVER_RFC.md` — soak-log audit
  invariant (G3) feeding `v3.12.0_soak_log_audit.json`.
- `tests/test_release_gate_soak_evidence.py` — regression
  guards on the gate semantics; updated in PR #587 to lock the
  evidence-subject-commit rule.
