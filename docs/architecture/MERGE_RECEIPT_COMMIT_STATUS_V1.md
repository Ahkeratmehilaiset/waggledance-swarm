<!-- SPDX-License-Identifier: BUSL-1.1 -->
# Merge Receipt -> GitHub Commit Status (V1)

**Status:** DORMANT, UNWIRED. Producer-authored (`fable-5`) under the S12 F1 Source
request of 2026-10-25; dual-RCO review; operator exact-head signature. **Changes no
runtime behavior and activates nothing.** Nothing reads this tool on the merge
path, no CI job runs it, and it performs no GitHub call.

## 0. One-paragraph summary

A bridge-consensus merge today leaves its MAGMA receipt only on the merging host.
GitHub shows the merge commit and nothing about who approved it or which receipt
justified it (external review 2026-10-25, finding "merge provenance invisible").
`tools/magma_receipt_commit_status.py` closes the *preparation* half of that gap
offline: given a verified receipt bundle and the expected head and base, it builds
the exact commit-status payload that would carry the receipt digest onto the merge
head, prints the equivalent `gh api` line as **text**, and can re-derive the same
status from one that somebody else fetched. Posting the status stays a separate,
operator-signed action; wiring the status as a required check is a later (a)-class PR.

## 1. Binding

The only accepted input is the bundle written by
`tools/write_bridge_consensus_merge_receipt.py`: a `manifest.json` with exactly one
entry whose payload carries `artifact_version = wd.bridge_consensus_merge_receipt.v0`.
Verification is delegated to `tools.verify_magma_receipt.verify_manifest`; the receipt
digest is `waggledance.core.magma.canonical.sha256_digest(receipt)`, the same value the
verifier reports as `receipt_hash`. No digest or verification code is duplicated here.
Any other shape, including ad-hoc operator-merge records, fails closed with
`unsupported_receipt_shape`.

## 2. Output (dry run, always)

```json
{"state": "success", "context": "waggledance/magma-merge-receipt",
 "description": "magma merge receipt <receipt sha256 prefix 16> rco=<rco identity> head=<head prefix 12>",
 "target_url": null}
```

plus `receipt_sha256`, the three identities (`build_lead`, `build_tools`, `rco`), the
`autonomous_gate_pass_claimed` value when the payload carries one, and
`gh_command_text`: `gh api --method POST repos/<owner>/<name>/statuses/<head> -f ...`.
The command is never executed. There is no apply mode in V1 and the module imports no
network, process or `os` module (asserted by an AST test).

## 3. Refusals (all fail closed, exit code 2)

| decision | condition |
|---|---|
| `invalid_input` | head or base not 40 lowercase hex; unreadable JSON; `repo` not `owner/name` |
| `receipt_verification_failed` | `verify_manifest` reports errors (listed verbatim) |
| `unsupported_receipt_shape` | not exactly one entry, or payload `artifact_version` differs |
| `head_mismatch` / `base_mismatch` | payload `head_sha` / `base_sha` differ from the expected values |
| `autonomous_gate_pass_claimed_not_boolean` | the flag is present but not `true`/`false` |
| `missing_identities` | verdict not `bridge_consensus_verified`; a role missing, unapproved or not distinct; `rco_pass_ref` names another agent |
| `author_is_voter` | `bridge_consensus.author_agent` holds any of the three slots |

## 4. `--verify`

`--verify <fetched.json>` takes a status object, or the list GitHub returns for a
commit, selects the single status with context `waggledance/magma-merge-receipt`,
and compares `state`, `context` and `description` with the re-derived status.
`EQUAL` exits 0; `DIFFERENT` exits 1 with each differing field named. Zero or several
statuses with the context is reported as a `context` difference. `target_url` is not
compared in V1 because the receipt is a local file.

## 5. Class and future wiring

Content class **(b)**: a dormant, unwired tool plus its tests and this spec. It does
not change CLAUDE.md, the charter, the contract or any module on the runtime verdict
path, and it grants nothing. The classifier's own verdict is recorded as printed in
the PR; a fail-closed (a) for an unlisted path is not disputed by this text.

Making the status a *required* check (branch protection) or posting it from the
merge executor wires the tool onto the merge path and is **(a)**: its own PR,
dual-RCO adversarial review and operator signature, after the operator decides the
account and token question that the external review raised. Until then the tool is
a preparation aid: the operator or an operator-signed script may run the printed
command by hand, and anyone may re-derive a posted status with `--verify`.

## 6. What this does not claim

It does not prove that a merge was gated: it carries an existing receipt's digest.
A consumer still re-derives the verdict from the receipt fields (identities, head,
`rco_pass_ref`) as the Rule 9a MAGMA clause requires; the status only makes the
receipt's existence and digest visible where the merge commit is.
