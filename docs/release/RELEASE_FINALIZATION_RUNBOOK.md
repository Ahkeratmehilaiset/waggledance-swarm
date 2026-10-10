# v3.12.0 Release Finalization Runbook

This runbook is the exact mechanical sequence to reach a release-gate
`decision: pass` on `docs/runs/release_soak_evidence/v3.12.0.json` once the
soak window completes. It is the only finalization recipe; do not improvise
status fields by hand.

## Verified starting state (main `28a98f34`, checked 2026-09-17)

Earlier revisions of this runbook described the stored evidence as
`result=hold` and the finalization as a `hold` to `pass` flip. That is no
longer the state on disk, and the difference matters, so it is recorded here
rather than left for the operator to discover mid-run:

* `docs/runs/release_soak_evidence/v3.12.0.json` **already stores**
  `result: "pass"`, all six status fields `pass`, and
  `docker_stable_policy: "finalized"`.
* The gate nevertheless returns `decision: "hold"` (exit code 1), because a
  stored `pass` is no longer sufficient on its own. The evidence must also be
  **rebuildable** from the local artifacts, and the Axis artifacts must carry
  source binding. Neither holds today.
* Re-running Step 1 against the stored evidence-subject commit
  `8db47f609cd3d838dbb67c94542921b391c1ac74` rebuilds
  `result: "hold"` and `docker_stable_policy: "draft"`, and adds an
  `artifact_selection` object the stored file does not have.

So the remaining work is **not** a status flip. It is producing artifacts the
collector can rebuild from. Do not close that gap by editing the JSON.

## Pre-conditions to verify

Before running the re-collect, confirm all five:

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
   - `artifact_selection` ⇒ present, an object naming the selected
     bandit and pip-audit artifacts with their `source_digest`. The
     collector emits this field and the reproducibility verifier compares
     it; a file without it can never verify. The stored file does not
     currently have it.
   - Passing this inspection does **not** mean the gate will pass. Every
     field above already reads as expected on main `28a98f34` while the gate
     still holds. Pre-condition 5 is the one that is currently failing.
4. **Reproducibility.** The stored evidence must rebuild from the local
   artifacts. Check it directly, before touching anything:
   ```bash
   python -c "import json; from tools.verify_release_soak_evidence import build_report; \
   print(json.dumps(build_report(soak_evidence='docs/runs/release_soak_evidence/v3.12.0.json', \
   release_readiness='docs/release/RELEASE_READINESS.md'), indent=1)[:2000])"
   ```
   Require `verified: true` and an empty `blockers` array. On main
   `28a98f34` this returns `verified: false` with nine blockers: three
   `field_mismatch:` entries (`artifact_selection`, `docker_stable_policy`,
   `result`) and six Axis source-binding entries (`axis_a_source_commit_missing`,
   `axis_a_generated_at_invalid`, `axis_a_sources_unbound`, and the three
   `axis_b_` equivalents). Each names a real artifact gap. Fix the artifact.
5. **Operator decision packs are signed.** Verify
   `docs/operator_inbox/torch-cuda-vs-cpu.yaml` and
   `docs/operator_inbox/docker-latest-promotion.yaml` both have a
   non-empty `operator_signoff.signed_by` and `chosen_option`. The
   docker pack must specify `chosen_option: ghcr_stable_only`
   (latest tag does NOT move at v3.12.0 stable).

   On main `28a98f34` both packs are signed: the torch pack carries
   `signed_by: "operator:jani:2026-05-22T18:14:34Z"` with
   `chosen_option: "A2_cu126"` plus a later scope-update signature
   (`operator:jani:2026-09-11T05:55:35Z`), and the docker pack carries
   `signed_by: "operator:jani:2026-05-22T18:14:34Z"` with
   `chosen_option: "ghcr_stable_only"`.

   A signed pack is **not** the same as a finalized artifact, and this is the
   trap in pre-condition 5. `docs/runs/release_soak_evidence/v3.12.0_docker_policy.json`
   still reads `docker_stable_policy: "draft"` with
   `operator_authorization: null` and a blocker of
   `operator_authorization_missing`, and it is bound to the stale commit
   `bbb0cc371c19884317b07b03bcaf8b1e42a46667`. That is why a re-collect
   derives `draft` even though the pack is signed. Regenerate the docker
   policy artifact against the real subject commit with the operator
   authorization recorded; do not pass `--docker-stable-policy finalized`
   to paper over it.

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

**On the `2026-05-24` end date.** The window above is the R22.5 calendar
window recorded in `docs/release/RELEASE_READINESS.md`. Elapsed May calendar
time does not demonstrate elapsed runtime for a source subject frozen in
September, and the release notes already require a fresh-subject soak before
stable. Running this command reproduces the recorded window; it does not by
itself satisfy that requirement, and old hours must not be relabelled as a
fresh-subject soak. Treat a `duration_hours: 336` derived from these two
timestamps as a schema value, not as proof that the candidate ran for 336
hours.

## Step 2 — Verify the gate accepts the new evidence

Pass the real current UTC date. The gate's `--today` exists to make checks
reproducible, not to pick a convenient day, and the anti-claims below forbid
coercing it.

```bash
python tools/check_release_gate.py \
  --release-readiness docs/release/RELEASE_READINESS.md \
  --soak-evidence docs/runs/release_soak_evidence/v3.12.0.json \
  --today "$(date -u +%F)"
```

The target output is `decision: "pass"` with an empty `blockers` array,
`latest_stable: "v3.8.0"`, and the `soak_window` block reporting
`start: "2026-05-10"`, `end: "2026-05-24"`, `required_hours: 336`.

That is the target, not the current behaviour. **Actual output on main
`28a98f34`, checked 2026-09-17, is `decision: "hold"` with exit code 1** and
these ten blockers:

```
soak_evidence_not_reproducible
field_mismatch:artifact_selection
field_mismatch:docker_stable_policy
field_mismatch:result
axis_a_source_commit_missing
axis_a_generated_at_invalid
axis_a_sources_unbound
axis_b_source_commit_missing
axis_b_generated_at_invalid
axis_b_sources_unbound
```

The same ten appear whether `--today` is `2026-05-24` or the real date, so
the calendar clause is not what is holding this release. The real output also
carries a `soak_evidence_diagnostics` object (with a nested
`soak_reproducibility` report) that earlier revisions of this runbook did not
mention; read it, because it names the mismatched fields directly.

`decision != "pass"` ⇒ STOP. Read the `blockers` array; each entry is
a fail-closed gate clause from `tools/check_release_gate.py`. What they mean:

- `soak_evidence_not_reproducible` ⇒ the umbrella blocker. The evidence did
  not rebuild from local artifacts. The `field_mismatch:` and `axis_*`
  entries below it are the specific reasons; fix those, not this.
- `field_mismatch:<field>` ⇒ the rebuilt value for `<field>` differs from the
  stored value. Currently `artifact_selection` (absent in the stored file),
  `docker_stable_policy` (rebuilds `draft`, stored `finalized`), and `result`
  (rebuilds `hold`, stored `pass`). Re-collect; do NOT hand-edit the JSON.
- `axis_a_source_commit_missing` / `axis_b_source_commit_missing` ⇒ the Axis
  artifact does not record the commit it was generated from.
- `axis_a_generated_at_invalid` / `axis_b_generated_at_invalid` ⇒ its
  `generated_at` timestamp is missing or unparseable.
- `axis_a_sources_unbound` / `axis_b_sources_unbound` ⇒ its source files are
  not hash-bound. Regenerate the Axis artifacts with source binding; this is
  artifact work, not a gate or status change.
- `soak_reproducibility_verifier_unavailable` ⇒ `tools/verify_release_soak_evidence`
  could not be imported. Fail-closed by design; fix the import, never skip it.
- `before_no_earlier_than_date` ⇒ system clock is wrong, or it is not
  yet 2026-05-24 UTC.
- `soak_evidence_ended_before_required_soak_end` ⇒ `ended_at_utc` is earlier
  than the required soak end, usually a wrong `--ended-at-utc`.
- `soak_evidence_duration_lt_<N>h` ⇒ the recorded `duration_hours` is below
  the required window. `<N>` is not fixed: the gate computes it from the
  readiness window and interpolates it, so with today's `2026-05-10` to
  `2026-05-24` window the emitted blocker reads exactly
  `soak_evidence_duration_lt_336h`. It is absent from the ten current blockers
  only because the stored evidence already records `duration_hours: 336`, not
  because the gate cannot produce it. A genuine short soak run will show it.
- `soak_evidence_<field>_not_<expected>` ⇒ a status field is not at its
  expected value, for example `soak_evidence_ci_status_not_pass`. Both halves
  come from `STATUS_PASS_FIELDS`, and all six of its expected values are
  currently the literal `pass`, so in practice this emits only
  `soak_evidence_<field>_not_pass` today. `<expected>` is interpolated rather
  than hardcoded, so it would follow if that table ever gained a non-`pass`
  value, but no such case exists now. Do NOT hand-edit the JSON; re-run
  `collect_soak_evidence` after fixing the underlying artifact.
- `soak_evidence_docker_policy_not_finalized` ⇒ `docker_stable_policy` is not
  `finalized`. This one is a plain literal from its own branch, not an
  instance of the pattern above, so a text search does find it.
- `soak_evidence_result_not_pass` ⇒ the collector did not derive
  `result=pass`; the most common cause is a missing
  `--use-local-artifacts` flag or stale artifact. Re-collect.
- `soak_evidence_unreadable:<ExceptionClass>` ⇒ the evidence file could not be
  read or parsed at all.

**Do not try to enumerate this list by grepping the gate for quoted strings.**
Several blockers are built with f-strings and interpolated values, so a literal
search will not find them and can make a real blocker look impossible. The
three built this way are the `duration_lt`, the `<field>_not_<expected>`, and
the `unreadable` entries above. To check whether the gate can emit a given
blocker, call `evaluate_release_gate` on a synthetic evidence file that should
trigger it and read the returned `blockers` array. An earlier revision of this
runbook asserted, from a literal grep alone, that `soak_evidence_duration_lt_336h`
could never appear. That was wrong, and it was caught in review by someone who
ran the function instead of grepping for it.

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
- An agent MUST NOT close a `field_mismatch:` blocker by editing the stored
  evidence to match the rebuild, or by passing `--status` /
  `--docker-stable-policy` overrides to force agreement. The mismatch means
  the artifact and the claim disagree; only the artifact may be fixed.
- An agent MUST NOT treat a signed operator decision pack as equivalent to a
  finalized evidence artifact. The docker pack is signed today and the docker
  policy artifact is still `draft`.
- The runbook does NOT cover hotfix releases or rollbacks; those have
  their own (yet-unwritten) procedures.

## Known gaps (recorded 2026-09-17, not fixed here)

* The Axis A and Axis B artifacts lack source-commit binding, a valid
  `generated_at`, and hash-bound sources. Six of the ten current blockers are
  this one gap. Regenerating them is the largest remaining item.
* `docs/runs/release_soak_evidence/v3.12.0_docker_policy.json` is bound to
  `bbb0cc37`, not to any current subject commit, and records no operator
  authorization.
* `docs/release/RELEASE_READINESS.md` documents the collector's `--history`
  path as `docs/release/soak_evidence_history.jsonl`, which does not exist in
  the tree. The real file is
  `docs/runs/release_soak_evidence/v3.12.0_history.jsonl`, which is what this
  runbook uses. Correcting `RELEASE_READINESS.md` is out of this document's
  scope and needs its own change.
* A dedicated fresh-soak verifier (`tools/verify_fresh_release_soak.py`) was
  in review but is not present on main `28a98f34`, so nothing in this runbook
  depends on it.

## References

- `tools/collect_soak_evidence.py` — evidence collector (writer).
- `tools/check_release_gate.py` — fail-closed gate (reader).
- `tools/verify_release_soak_evidence.py` — reproducibility verifier. The
  gate imports its `build_report` after structural validation and fails
  closed if it is unavailable, raises, or returns `verified != true`. This is
  what currently holds the release.
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
