# 72H WD P4 Standing-Sign B-Class Probe - 2026-06-30

Status: candidate.
Sprint seed: P4 seed #5, first real standing-sign `(b)`-class proof.
Owner: codex-lead-1.
Base: `origin/main` at `56119e261d7b9343edbf780d105e1675c70506ad`.

This artifact is intentionally small and reversible. It exists to create a
clean `(b)`-class off-allowlist PR that can prove the standing-consensus-sign
path by its merge receipt, not by assertion inside this document.

## Why This Exists

PR #1438 is useful as a sprint truth dashboard, but it is not a valid seed #5
standing-sign candidate. The local classifier returns `(a)` for #1438 because
the new `tools/build_wd_p4_sprint_truth_dashboard.py` and
`tests/tools/test_wd_p4_sprint_truth_dashboard.py` paths are unrecognized by the
closed `(b)` allowlist.

This probe keeps the changed path set to `docs/runs/` only:

- `docs/runs/72h_wd_p4_standing_sign_b_class_probe_20260630.md`

The standing-sign classifier recognizes `docs/runs/*.md` as a reversible
`(b)` category, provided no `(a)` or unrecognized path is mixed into the PR.

## Success Criteria

The probe succeeds only if the PR carrying this file lands without a per-PR
operator merge and the post-merge receipt can be re-derived from bridge and CI
evidence.

Required evidence:

- changed-path classification is `(b)` at the exact head;
- GitHub CI is all green at the exact head;
- bridge consensus is head-exact;
- lead build slot is either a valid non-author pass or an explicit build-author
  waiver, as computed by the gate;
- tools build consensus is present at the exact head;
- `claude-rco-1` and `claude-rco-2` both pass at the exact head;
- no recognized RCO veto remains active at the exact head;
- the autonomous merge receipt records
  `operator_signature=satisfied_by_standing_consensus_sign`;
- the receipt replay canary can re-derive the same standing-sign admission.

If any item is missing, stale, or ambiguous, the probe is not complete. A green
CI result or this document alone is not success.

## Authority Boundary

This PR must not grant, imply, or request any of the following:

- bridge append authority;
- queue write authority;
- scheduler enqueue authority;
- merge authority;
- rollback execution authority;
- runtime mutation authority;
- transport activation;
- production activation.

All authority remains with the existing bridge, CI, RCO, and merge gates.

## Local Preflight Commands

The expected local classifier preflight is:

```powershell
$env:PYTHONPATH=(Get-Location).Path
@'
from tools.check_standing_consensus_sign_class import classify_ab
print(classify_ab([
    "docs/runs/72h_wd_p4_standing_sign_b_class_probe_20260630.md",
]))
'@ | python -
```

Expected result: `ab_class` is `b`, with no `a_hits` and no `unrecognized`
paths.

The standing-sign conformance test remains the relevant regression guard:

```powershell
python -m pytest tests/tools/test_standing_consensus_sign_class.py -q
```

## Current Sprint Impact

Before this probe merges, WD readiness does not increase. After a successful
standing-sign merge with a replayable receipt, seed #5 can be counted as
complete and the sprint dashboard may move the conservative WD readiness toward
the sprint target. Runtime activation remains false either way.
