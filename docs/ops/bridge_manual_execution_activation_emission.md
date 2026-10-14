# Cold-manifest manual execution approval emission

This Source change adds an optional `-ManualExecutionApprovalRecordPath` to
`ops/windows/reboot/Deploy-WdRebootBundle.ps1`. It is not an activation, install,
merge, restart or Stage-2 approval. Omission leaves the default manifest without
`manual_execution`; the existing manual-execution admission guard still refuses.

The input is a bounded (1–16384 bytes), strictly decoded UTF-8 JSON file at an
absolute persistent C: path without reparse ancestors. Its closed schema has
exactly these ASCII fields:

| Field | Required value |
| --- | --- |
| `schema` | `wd.bridge-manual-execution-activation.v1` |
| `enabled` | JSON boolean `true`, not a string or number |
| `agent` | An exact recognized agent present in the selected fleet |
| `source_commit` | The selected, committed and pushed lowercase 40-hex source |
| `definition_sha256` | The actual selected package definition's uppercase SHA256 |
| `approval_reference_sha256` | Explicit 64-hex reference to the operator decision |
| `tools` | Array containing exactly `tools/merge_with_bridge_receipt.py` and `tools/write_bridge_consensus_merge_receipt.py` |

Missing, duplicate, case-aliased, non-ASCII or unknown keys and wrong field types
are rejected. Both tools must be in the actual selected pinned package and match
its declared manual capability. The emitter derives source and definition from
that package after verifying the caller's agreement; it does not copy arbitrary
approval fields into the manifest. A decision hash is a reference, not proof
of a signature or new authority.

Before reading bytes, the opened handle's resolved path must match the checked
canonical path. An ancestor redirect between checking and opening is rejected;
noncanonical path aliases may also fail closed. The bounded read uses that same
read-only handle, not a reopened pathname.

The exact archived `BridgeLogReader.ps1` supplies the existing strict JSON
validator. No dependency, signing key, trust root, unsigned activation sidecar,
clock-based admission or automatic merge scheduler is added. Admission retains
the existing external manifest hash anchor, inherited agent/runtime identity,
fixed driver and argument checks. Rule 12 argument parity is a separate Source
dependency; this emitter does not alter `BridgeCodeContext.ps1` or a gate.

After separate approval, `-StageOnly -ManualExecutionApprovalRecordPath <path>`
may produce a candidate source/manifest pair without replacing machine wrappers.
This document does not authorize that staging or an install. The final install
still requires the separately approved exact `-ExpectedFinalCommit` and
`-ExpectedFinalManifestHash`, plus the same approval input. A new lane inherits
the new generation only through the approved cold-install/restart procedure.
No live manifest edit or hot repin is supported.

An existing commit-addressed target must contain exactly the requested optional
activation. Reusing an activated target while omitting approval, adding approval
to an already staged inactive target, or changing actor/reference fails closed.
The installer does not rewrite that target. A mismatch needs a separately
approved new source/package; never delete or overwrite an installed bundle to
work around it.

`-DryRun` returns before checking an existing target's activation binding. It
validates candidate emission, not the reusability of an already staged target.

Source acceptance uses isolated PS5/PS7 fixtures, negative binding twins and
named mutants. A fixture reaching the unchanged admission helper does not show
live gate readiness or approve execution. Exact-head CI, build decisions,
independent review and any required protected-class signature remain necessary;
actual supported no-apply evidence follows only after separately authorized
cold activation. Rule 10 and Stage-2 are unchanged.
