# Manual bridge merge and MAGMA execution capability

This is launch capability, not a merge approval. The protected merge executor,
receipt writer, Rule 9a evaluator, charter, recognized RCO identities and blocking
findings, and Rule 12 Grok switch are unchanged. Under the default review policy,
CI, independent RCO_PASS, both Codex build approvals and all existing per-merge
checks remain mandatory; an admitted `rule12` policy changes that approval
coverage as described below.

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

The inherited `AGENT_BRIDGE_RUNTIME_ROOT` must explicitly match the absolute
`WD_BRIDGE_RUNTIME_ROOT` lane context, and any `AGENT_BRIDGE_ROOT` alias must
match it too. The child receives that validated context for both selectors;
missing, redirected or reparse runtime roots refuse. Manual `--events` and
`--bridge-root` overrides are not admitted. These are existing same-user host
context pins, not independent authentication of the event log.

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
lowercase 40-character SHAs. `--review-policy` admits only the exact existing
executor values `bridge_consensus_v1` and `rule12`, with either a separate value
or `--review-policy=rule12`. Omission preserves the executor's current default,
`bridge_consensus_v1` (the Rule 9a verifier). The marker and policy argument
reach the same pinned driver with the same manifest, activation, lane and
runtime bindings. Admitting the argument approves no gate verdict, but `rule12`
changes which approvals can satisfy the gate: it replaces the default Rule 9a
verifier with the Rule 12 evaluator. There, one opposite-family approval (from a
lane outside every implementer's model family) takes the place of both Codex
build approvals. The RCO slot needs an exact-head `rco_pass` from two distinct
eligible recognized RCOs when at least two are present, or from the one that
is. When none is present (each is an implementer, self-recused, not provisioned
or absent after an unanswered bound review request), an attested pool fallback
can hold the RCO slot: the first eligible of `codex-tools-1`, `codex-lead-1` and
`fable-5` whose exact-head `rco_pass` carries the pool attestation and that has
no uncleared block of its own. Selecting `rule12` is therefore part of what a
future activation decision authorizes; this source change activates no policy.
`--now`, operator path-exception arguments and `--grok-fallback` remain refused,
so the Rule 12 Grok fallback is not reachable through this route. No
`--admin`, `--no-verify` or force-push option is admitted. Without `--apply` the
existing merge driver keeps its dry-run behavior; a dry run is not a merge.

This policy-argument parity is available only in a newly built, verified and
separately authorized generation containing this source change. An older
installed wrapper may reject it before Python. Never alter a live bundle or
session pin to obtain it, and never call the executor outside the pinned wrapper.

`--out-dir` must be a new or empty directory at an absolute canonical C-drive
path below `.codex-audit`. The receipt status input must be an existing regular
file in an audit area. UNC paths, NTFS streams, dot-segment aliases, trailing
dot/space aliases and reparse ancestors refuse before Python. This protects
artifact placement; it does not authenticate a caller-supplied status document.

The receipt driver uses the same marker/bindings and additionally requires
`--pr-status-file`; it admits no `--apply` or positional PR argument. It does
not perform a merge. Its standalone receipt output is non-authoritative:
it reads a caller-supplied status file and must not be used as merge authorization
or proof of live GitHub checks. Retain its output as audit material only. The
merge executor obtains its own live status snapshot and rechecks the gate before
merging; only that executor's receipt/preflight is used by this launch route.
The executor's own MAGMA preflight and recorded receipt
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
Policy parity has PS5/PS7 positive twins for both drivers, both exact policy
values and both argument forms, plus missing/invalid/duplicate/abbreviated and
separator negative controls. A mutant that removes `--review-policy` from
admission is killed by the Rule 12 positive regression. No gate, charter,
Grok-switch, activation record or package definition is changed.

Nothing here authorizes installation, repinning, runtime activation, release or
Rule 10 / Stage-2 cutover. Source merge and installed runtime are separate states.
