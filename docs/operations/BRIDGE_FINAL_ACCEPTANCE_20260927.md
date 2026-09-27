# Bridge final acceptance: single-signature boundary

Status: preparation, not approval or deployment authorization.

The integration vehicle is PR #1751, `codex-lead-1/bridge-final-20260927`.
The operator requested one final approval after the swarm finishes construction,
testing and independent review. Earlier approvals of other PRs do not authorize
this integration. Intermediate heads and slice reviews are not final approvals.

## Required evidence before presenting the signature

1. Freeze the complete candidate. Record its full head, tree, main/base commit,
   constituent commits and exact changed paths. Preserve the unrelated dirty
   original worktree. Reconcile active claims before transferring source scope.
2. Record commands, platform, configuration and results for affected regressions.
   Exercise Windows PowerShell 5.1 and PowerShell 7 where supported; a Linux skip
   is not Windows evidence. Keep failed, cancelled and superseded runs distinct.
3. Require all required GitHub checks at that exact combined head to succeed.
   Collect independent RCO1 and RCO2 decisions and Lead/Tools build consensus at
   the same head. Resolve every veto; no slice vote substitutes for this step.
4. Stage the bundle in isolated audit output using the installer/runbook under
   Windows PowerShell 5.1. Verify the required helper set and every manifest hash.
   Do not activate wrappers, scheduled tasks, the reboot pointer or live lanes.
5. Present release notes, their SHA256, the unused proposed release tag, staged
   evidence and a bounded rollout plan. List intentional policy changes and
   unresolved limitations explicitly, not under a generic "all done" label.

Important regression axes include request/reply envelope parity; exact binding
and nonce rejection; UTC and canonical append order; repeated request handling;
requester-only cancellation; retained queue versus canonical delivery receipts;
session-owned claims across PowerShell/Python; runtime-root and code-pin
separation; launcher marker isolation; containment; and supervisor ownership.
Machine-wide Windows mutex interoperability must be measured with actual command
tokens, not inferred from parent process names. Expired probes prove nothing
about cross-integrity access. No live lock ACL change is part of preparation.

## What the single approval must bind

The final direct operator instruction must include the complete head and tree,
current main/base, tag, release-notes SHA256, and explicit delegation to the named
executor `codex-lead-1` for manual merge plus conditional release and rollout.
It must identify denylisted bridge infrastructure changes and any intentional
security or recovery-policy changes. Do not treat a peer relay or an agent's
`agent=operator` event as an operator signature.

The charter's automatic merge path cannot admit denylisted bridge paths through
`operator_path_exception`. Do not alter that gate or manufacture a MAGMA receipt.
If explicitly delegated in the eventual signature, the manual command is:

```text
gh pr merge 1751 --repo Ahkeratmehilaiset/waggledance-swarm --squash --match-head-commit <signed-full-head>
```

No admin or force options. Recheck current head/base, canonical RCO decisions,
build consensus and veto absence immediately beforehand. A changed head or base
invalidates approval; do not silently rebase. Record the verbatim instruction,
UTF-8 hash, observed channel/time and actual effects in a
`wd.operator-signed-merge.v1` audit. This is session-observed authorization, not
cryptographic authentication or autonomous consensus authority.

## Conditional success path after approval

1. Verify the resulting main tree equals the signed candidate tree. Wait for
   both Tests and WaggleDance CI on the resulting main commit. Failure means no
   release or deployment, even if the manual merge already completed.
2. Stage that main commit and compare file set/content hashes against reviewed
   staging. Only documented commit-addressed metadata may differ. Publish the
   reviewed release/tag at the verified main commit; do not dispatch Docker or
   stable release workflows.
3. Activate through the verified installer/runbook and update the reboot pointer.
   Re-pin `WD-AgentValue-Weekly`, preserving its principal and enabled state.
4. Obtain fresh checkpoints and idle evidence. Restart one lane at a time:
   RCO1, RCO2, Tools, Lead, Fable last. Never stop both RCOs together. Choose clean
   versus resumed startup from current persistence evidence; do not repeat old
   transcript archival. Before Lead's restart, persist exact continuation and
   completed effects to prevent replay.
5. Verify generation, manifest pin, PID plus start time, transcript growth and a
   bound bridge exchange per lane. Check actual Claude process marker absence,
   one Tools owner and the scheduled-path supervisor result. An opaque healthy
   native terminal may remain UNVERIFIED; never label it verified ownership.

Stop on a failed check and report completed effects. Keep prior bundles/backups.
Do not discard new work during rollback. A source revert needs a new reviewed PR
and approval; the success-path signature does not authorize arbitrary recovery.

Preserve the merge-driver HOLD. Excluded work includes Stage-2 cutover,
HUMAN_APPROVAL collection, new runtime authority, `claim_safe` or
`consensus_grade` upgrades, and unrelated historical/application PRs. Report only
the measured continuity interval; no promise of future uninterrupted operation.
