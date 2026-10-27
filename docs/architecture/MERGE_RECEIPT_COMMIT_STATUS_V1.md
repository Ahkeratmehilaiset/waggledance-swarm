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
`autonomous_gate_pass_claimed` value when the payload carries one, `status_sha` and
`gh_command_text`: `gh api --method POST repos/<owner>/<name>/statuses/<status_sha> -f ...`.
The command is never executed. There is no apply mode in V1 and the module imports no
network, process or `os` module (asserted by an AST test).

**Two commits, one receipt.** `status_sha` defaults to the PR head, where the status is
visible on the PR before the merge. The repo merges by squash
(`tools/merge_with_bridge_receipt.py`), so the commit that lands on `main` is a different
SHA (PR #1931: head `7ed1d5b0`, main `6b33c6f9`). After the merge the same status is
built once more with `--status-sha <merge commit>`; the description then also carries
`merge=<prefix>` while `head=` keeps the receipt's head, so both commits point at the
same receipt digest. The merge commit is not known before the merge, which is why V1
takes it as an argument instead of reading it from the receipt.

## 3. Refusals (all fail closed, exit code 2)

| decision | condition |
|---|---|
| `invalid_input` | head, base or `status_sha` not 40 lowercase hex; unreadable JSON; `repo` not `owner/name` |
| `receipt_verification_failed` | `verify_manifest` reports errors (listed verbatim) |
| `unsupported_receipt_shape` | not exactly one entry; payload `artifact_version` differs; a `review_policy` (Rule 12 receipts) is present, which V1 does not model |
| `head_mismatch` / `base_mismatch` | payload `head_sha` / `base_sha` differ from the expected values |
| `autonomous_gate_pass_claimed_not_boolean` | the flag is present but not `true`/`false` |
| `missing_identities` | verdict not `bridge_consensus_verified`; a role missing, unapproved or not distinct; `rco_pass_ref` names another agent |
| `author_unknown` | `bridge_consensus.author_agent` is empty, so author vs voter cannot be checked |
| `author_is_voter` | `bridge_consensus.author_agent` holds any of the three slots |
| `invalid_input` (`--verify`) | a fetched status `url` is not `repos/{owner}/{name}/statuses/{sha}`; a combined object's `sha` is not 40 lowercase hex |
| `target_repo_mismatch` (`--verify`) | a fetched status `url` names a repository other than the receipt's `repo` |
| `conflicting_target_proof` (`--verify`) | the fetched data carries two target proofs (combined `sha`, status `url`s) naming different commits |

The manifest path is resolved before verification and loading, so a symlinked manifest
reads payload and receipt from the verified bundle directory, never from files beside the
link.

## 4. `--verify`

`--verify <fetched.json>` takes one status object, the `repos/{repo}/commits/{sha}/statuses`
list (newest first; a reposted history is allowed and the newest entry with context
`waggledance/magma-merge-receipt` is compared, `matching_statuses` reports how many there
were) or the combined `repos/{repo}/commits/{sha}/status` object (one entry per context
under `statuses`). It compares `state`, `context` and `description` with the re-derived
status; pass the same `--status-sha` that was used to post. `target_url` is not compared
in V1 because the receipt is a local file.

**Payload equality is not target proof (F1-VERIFY-TARGET, PR #1934 review).** The same
status text can sit on any commit, so the fetched data must also prove *which* commit it
was fetched for. The proof is what GitHub returns with the status: the combined object's
`sha`, and every status' `url` (`https://api.github.com/repos/{owner}/{name}/statuses/{sha}`).
The tool binds that commit to the requested `status_sha` (the PR head by default, the
squash merge commit with `--status-sha`) and reports it as `target_proof`:

| decision | exit | condition |
|---|---|---|
| `EQUAL` | 0 | proof names `status_sha` **and** the compared fields match |
| `DIFFERENT` | 1 | proof names another commit (difference `target_commit`, expected vs fetched), a compared field differs, or no status carries the context (difference `context`) |
| `UNKNOWN_TARGET` | 1 | no proof at all (`target_proof` null): a bare status list, a bare status object or a combined object without `sha`; compared-field differences are still listed, but the comparison is unbound and is never `EQUAL` |
| refusal | 2 | `conflicting_target_proof`, `target_repo_mismatch`, `invalid_input` (section 3) |

Head vs squash commit stays explicit: a merge-commit status (`merge=` in its description)
fetched from the PR head, verified with `--status-sha <merge commit>`, is `DIFFERENT` on
`target_commit` even though its text is right; the same status fetched from the merge commit
is `EQUAL`. A status fetched from the merge commit but verified without `--status-sha` is
`DIFFERENT` on both `target_commit` and `description`.

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
`rco_pass_ref`) as the Rule 9a MAGMA clause requires. The status makes the receipt's
existence and digest visible on the PR head and, only after the second post with
`--status-sha`, on the squash merge commit in `main`'s history; a status on the head
alone says nothing about `main`. Rule 12 (best-available-consensus) receipts are out of
scope for V1 and are refused by name.
