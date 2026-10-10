# Manual bridge merge and MAGMA execution capability

This is launch capability, not a merge approval. The protected merge executor,
receipt writer, Rule 9a evaluator, charter, recognized RCO identities and blocking
findings, and Rule 12 Grok switch are unchanged. CI, independent RCO_PASS, both
Codex build approvals and all existing per-merge checks remain mandatory.

## Default and activation boundary

`bridge-code-files.json` declares exactly two manual drivers separately from
`python_entrypoints`: `tools/merge_with_bridge_receipt.py` and
`tools/write_bridge_consensus_merge_receipt.py`. Declaration does not activate
them. All six gate executors remain unavailable to ordinary wrapper invocation,
even if accidentally inserted into ordinary entrypoints. No idle scheduler or
background merge is published.

Manual admission requires an explicit first argument `--wd-manual-execution`
and an enabled `manual_execution` object in the deployment manifest:

```json
{
  "schema": "wd.bridge-manual-execution-activation.v1",
  "enabled": true,
  "definition_sha256": "<uppercase SHA256 of deployed bridge-code-files.json>",
  "source_commit": "<full lowercase bundle source SHA>",
  "agent": "codex-lead-1",
  "tools": [
    "tools/merge_with_bridge_receipt.py",
    "tools/write_bridge_consensus_merge_receipt.py"
  ],
  "approval_reference_sha256": "<SHA256 of the operator activation decision>"
}
```

The record has exactly those fields. Its agent must name one existing fleet
lane and match both `AGENT_BRIDGE_AGENT` and `--from-agent`. Its source commit
must match the manifest. The entire manifest must match the already inherited
`WD_REBOOT_EXPECTED_MANIFEST_HASH`; missing external anchors refuse manual
execution. The inherited interpreter hash pin is mandatory for manual calls.
Definition, driver, wrapper, interpreter and the complete package
dependency closure retain their existing hash/reparse checks.

There is no new trust root, key, signer, credential or unsigned sidecar. The
approval-reference hash is an audit reference, **not authentication of a human**.
Activation authority comes through the existing external manifest anchor and
the operator's separately verified decision. Same-user processes can alter an
environment/anchor; this retains the existing host trust boundary and does not
claim to prevent an agent from acting directly on the shared GitHub account.

The default deployer does not emit this activation record. This PR changes
neither the deployer nor any installed bundle. Approved activation metadata
materialization and its externally anchored manifest must be prepared in a
separately authorized staging/deployment workflow. Never patch a live pinned
bundle or replace an inherited pin to obtain the capability.

## Conscious manual invocation after authorized activation

Use the inherited pinned wrapper, never a worktree-relative helper or bare
Python. A consciously initiated merge, after independently verifying the exact
PR's consensus, CI, mergeable state and charter, has this shape:

```powershell
& $env:WD_BRIDGE_PYTHON_WRAPPER tools/merge_with_bridge_receipt.py `
    --wd-manual-execution <PR_NUMBER> `
    --repo Ahkeratmehilaiset/waggledance-swarm `
    --expected-head <FULL_HEAD_SHA> --expected-base-sha <FULL_BASE_SHA> `
    --from-agent codex-lead-1 --bridge-task-id <CANONICAL_TASK> `
    --consensus-proposal-id <PROPOSAL_ID> --out-dir <AUDIT_RECEIPT_DIRECTORY> `
    --method squash --apply --json
```

The marker is stripped before Python. Canonical long options only: duplicate,
abbreviated and unknown options refuse before Python. Head/base must be full
lowercase 40-character SHAs. `--now`, operator path-exception arguments,
`--review-policy` and `--grok-fallback` are not admitted by this route. No
`--admin`, `--no-verify` or force-push option is admitted. Without `--apply` the
existing merge driver keeps its dry-run behavior; a dry run is not a merge.

The receipt driver uses the same marker/bindings and additionally requires
`--pr-status-file`; it admits no `--apply` or positional PR argument. It does
not perform a merge. The executor's own MAGMA preflight and recorded receipt
must identify the actual three approval identities, head and RCO_PASS event.
Never substitute a receipt or approval after any gate refusal. Existing nonce,
expiry and bound-approval checks keep their original contracts; this launch
capability does not introduce another replay or signing protocol.

## Bootstrap and tests

This (a)-class capability PR remains draft until the operator decides otherwise.
Its author cannot independently approve it. Require a non-author build review,
independent recognized RCO_PASS, exact-head green CI and no active recognized
blocking finding, then present the operator one exact head/tree/consensus
signature request for merge and activation. This implementation authorization
is not that signature. Protected or denylisted PRs still require their separate
explicit operator signature; the ordinary gate is not bypassed by this feature.

Tests launch only inert synthetic drivers on Windows PowerShell 5.1 and pwsh 7.
They check actual wrapper admission and package checks, not fabricated gate
PASSes or live GitHub merges. Existing executor/receipt and request-contract
regressions separately cover their safety contracts. Preserve committed-head
raw logs, commands, native exit codes and SHA256s outside the signed tree.

Nothing here authorizes installation, repinning, runtime activation, release or
Rule 10 / Stage-2 cutover. Source merge and installed runtime are separate states.
