# Manual Bridge Merge A (MANUAL-A)

**Status:** partial delivery. NOT ready and NOT in use. Draft PR #1763,
branch `manual-a-20261005`, base `c5f7c933`. Production stays on `d5864fdd`
and the release stays held.
**Authorization source:** operator decision 8A480508 (2026-10-05), Lead
implementation plan F9D23F10.
**Companion docs:** `CLAUDE.md` (Rules 6, 9, 9a, 10, 11),
`BRIDGE_CONSENSUS_APPROVAL_V1.md`, `RCO_PASS_PRESENCE_GATE.md`.
**This document does not:** change any gate, authorize any merge, ask for or
contain a signature, or contain any key material.

## 1. Purpose (target architecture)

MANUAL-A is a manual merge route for (a)-class pull requests. The intended
flow is:

1. the operator signs one exact-head statement with an SSH key;
2. the route verifies that statement against a trust anchor read from the
   trusted base commit;
3. the route checks the three real bridge approvals and the absence of any
   recognized-RCO veto;
4. the route merges exactly that head once;
5. the route writes a MAGMA receipt from which a reader can re-derive the
   verdict.

Steps 3 to 5 do not exist yet. Section 2 separates what is delivered from what
is only planned.

## 2. Delivered slice vs. target

| Component | Path | State in this PR |
|---|---|---|
| Statement contract, trust-anchor loader, SSH verify, nonce ledger | `tools/manual_bridge_merge_statement.py` | Delivered. Unit-tested with injected runners only. |
| Live diff facts (G1): signed paths and diff digest vs Git | `tools/manual_bridge_merge_statement.py` | Delivered (section 5a). Unit-tested with injected runners and with real `git` on a throwaway repository. |
| Receipt contract and writer | `tools/manual_bridge_merge_receipt.py` | Delivered. A genuine receipt is always refused. |
| Tests for those two modules | `tests/tools/test_manual_bridge_merge_statement.py`, `tests/tools/test_manual_bridge_merge_receipt.py` | Delivered. The evidence class is `unit_mock`. |
| Admission, preview and execute | `tools/manual_bridge_merge.py` | NOT delivered. |
| Merge-module tests | (planned with the admission module) | NOT delivered. |
| Trust anchor | `ops/security/manual-merge.allowed_signers` | NOT delivered. Absent at main `c5f7c933` and in this PR, so the anchor is UNKNOWN. |
| DN-A control admission (veto, unknown and replay controls) | (planned inside the admission module) | NOT delivered. An initial contract exists only as an audit proposal. |
| Live integration prerequisites | (see section 6) | UNKNOWN. |
| This document | `docs/architecture/MANUAL_BRIDGE_MERGE_A.md` | This file. |

What the delivered code does **not** do:

- it does not admit, preview, execute or merge anything;
- it does not read or write the bridge, call GitHub, or call any existing gate
  code;
- it cannot produce a genuine receipt.

Positive SSH proof is NOT_RUN: no real operator key has signed a statement
that a real `ssh-keygen` then verified. Every live proof is also NOT_RUN.

## 3. Statement contract (delivered)

Constants in `tools/manual_bridge_merge_statement.py`:

| Name | Value |
|---|---|
| schema | `wd.manual-merge-a.statement.v1` |
| SSH namespace | `waggledance-manual-merge-a` |
| principal | `operator@waggledance` |
| purpose | `manual-merge-receipt` |
| repository | `Ahkeratmehilaiset/waggledance-swarm` |
| trust-anchor path | `ops/security/manual-merge.allowed_signers` |
| merge method | `squash` |
| operation scope | `merge-single-pr` |
| allowed key types | `sk-ssh-ed25519@openssh.com` (label `ED25519-SK`), `ssh-ed25519` (label `ED25519`) |

The statement has a fixed field order:

`schema`, `namespace`, `principal`, `purpose`, `repository`, `pull_request`,
`head_sha`, `base_sha`, `diff_digest_sha256`, `exact_paths`, `merge_method`,
`batch_id`, `batch_order`, `dependencies`, `operation_scope`,
`expires_at_utc`, `nonce`, `allowed_signers_path`,
`allowed_signers_blob_sha`, `key_fingerprint`.

Its bytes must be canonical:

- UTF-8 without a BOM;
- no CR;
- a single line with exactly one trailing LF;
- no duplicate keys.

Any other representation is refused with `non_canonical_bytes`. A statement
whose `exact_paths` include the trust-anchor path is refused with
`allowed_signers_changed`.

## 4. Trust anchor (base-only)

**Source.** The anchor is read only from the trusted base commit, never from
the PR head and never from a file in a working tree. There is no fallback.
The loader runs exactly these commands:

1. `git rev-parse --verify --quiet <base>^{commit}`
2. `git rev-parse --verify --quiet <base>:ops/security/manual-merge.allowed_signers`
3. `git cat-file -t <blob>`
4. `git cat-file blob <blob>`

It then recomputes the git object id of the bytes it read, and that id must
equal the blob id.

**Content.** The file must contain exactly one non-comment line, made of:

- the principal `operator@waggledance`;
- the options, exactly `namespaces="waggledance-manual-merge-a"`;
- one of the two allowed key types;
- the base64 public key;
- an optional printable-ASCII comment.

The file must be ASCII with no BOM, CR or NUL, and at most 16 KiB.

**Fingerprint.** The fingerprint is `SHA256:` followed by the unpadded base64
SHA-256 of the key blob. This is the same form OpenSSH prints.

**Refusals:**

- `anchor_base_unknown`, `anchor_missing`, `anchor_invalid`,
  `anchor_key_type_not_allowed`, `anchor_integrity_mismatch`: the anchor
  cannot be loaded or is malformed;
- `anchor_not_from_statement_base`, `anchor_blob_mismatch`,
  `key_fingerprint_mismatch`: the statement does not match the anchor;
- `allowed_signers_changed`: the statement or the live PR diff changes the
  anchor.

**State today.** The anchor does not exist on main or in this PR, so the anchor
is UNKNOWN.

- Adding it is a Lead-owned (a)-class step. It needs the operator's single
  public key line; see section 8.
- Plan F9D23F10 lists it as conditional.
- The verifier can use an anchor only once it is in the trusted base. It
  refuses any PR whose diff changes the anchor path.

## 5. Signature verification (delivered, unit-tested only)

**Command.** The verifier runs this argument list, without a shell and with a
30-second timeout:

```
ssh-keygen -Y verify -f <temporary copy of the base anchor> -I operator@waggledance -n waggledance-manual-merge-a -s <temporary copy of the signature>
```

The exact statement bytes go to stdin.

**Accepted output.** The only accepted output is exactly one line:

```
Good "waggledance-manual-merge-a" signature for operator@waggledance with <ED25519-SK or ED25519> key <fingerprint>
```

A non-zero exit is refused with `signature_invalid`. Any other output is
refused with `signature_output_unexpected`.

**Binding.** `check_statement_binding` requires all of the following:

- the statement base equals the anchor commit and the live base;
- the statement head equals the live head (otherwise `signed_head_stale`);
- the anchor blob and the fingerprint match;
- `live_diff` (required, no fallback) is a `GitDiffFacts` for exactly the
  expected base and head (`invalid_live_fact` otherwise);
- the live diff does not touch the anchor;
- the live paths equal the signed `exact_paths` (`exact_paths_mismatch`);
- the live digest equals the signed `diff_digest_sha256`
  (`diff_digest_mismatch`);
- the statement has not expired (`statement_expired`).

`verify_statement` reads `live_diff` itself with `read_git_diff_facts`; the
caller cannot supply a path list. A mismatch refuses before the SSH
verifier runs.

### 5a. Diff digest contract (G1, delivered)

`read_git_diff_facts` first confirms that both ids are full lowercase
commit ids that git resolves to themselves, then runs exactly:

```
git -C <repo> --no-replace-objects diff-tree -r -z --raw --full-index --no-abbrev --no-renames --no-ext-diff --no-textconv --no-color --ignore-submodules=none <base_sha> <head_sha>
```

- It runs without a shell, with every `GIT_*` environment variable removed
  and replace objects disabled. The records come from the two commits'
  trees.
- Gitlinks (G1-F1). Without `--ignore-submodules=none`, git dropped
  mode-160000 gitlink records when `submodule.<name>.ignore` was `all` in the
  repository config, or when `.gitmodules` said `ignore = all` in the
  working tree, or, with no working-tree copy, in the index or `HEAD`. An
  added or changed gitlink then produced no path and the empty digest. The
  option overrides all of these, so every gitlink change is a record. Git
  may still read those settings and files; they no longer change the bytes.
- Configuration. On git 2.54.0.windows.1 these repository settings did not
  change the bytes: `diff.orderFile`, `diff.relative`, `core.quotePath`,
  `diff.noprefix`, `diff.mnemonicPrefix`, `diff.renames`, `diff.algorithm`,
  `diff.ignoreSubmodules`, `color.ui`, `core.ignoreCase` and
  `diff.external`. This is evidence for those keys on that version, not a
  proof for every setting or every git version. A setting that only
  reorders or reformats the bytes makes the signer's and verifier's digests
  differ, so the check refuses. A setting that hides records, as the
  submodule settings did, could hide a real change; for settings and git
  versions not listed here that risk is UNKNOWN.
- `diff_digest_sha256` is the lowercase hex SHA-256 of the exact stdout
  bytes. Each raw record holds both modes and both full blob ids, so a
  content change or a mode-only change alters the digest. A rename appears
  as a delete plus an add.
- The paths are the NUL-separated path fields, decoded as strict UTF-8,
  each passing the statement's path rules, unique and then sorted.
- Refusals: `invalid_live_fact` (malformed ids or repository path),
  `live_commit_unknown`, `live_diff_unavailable` (non-zero exit),
  `git_unavailable` (timeout or spawn failure) and `live_diff_malformed`
  (any record that is not an `A`, `D`, `M` or `T` raw record with a valid
  path, a non-UTF-8 path, or a duplicate).
- The signer computes the same digest with the same argv on the same
  commits. Use a shell that keeps stdout bytes unchanged (section 9).
- Limit: the digest proves only which bytes were compared. Protected base
  identity, live PR metadata, a re-read immediately before any effect,
  the human signature and the nonce state stay mandatory in the admission
  module (not delivered).

**One-time use.** `verify_statement` is preview-safe and consumes no nonce.
One-time use is enforced by the nonce ledger, and only the admission module
(not delivered) would drive it.

- Ledger states: `reserved`, `refused_before_effect`, `merge_started`,
  `executed`, `indeterminate`, `reconciled_merged`, `reconciled_not_merged`.
- The ledger refuses link, junction and hardlink aliases of its root, the
  root's ancestors, the lock file and the nonce files, but only at sampled
  checks: at construction and at the start of each operation (root), when a
  file is opened, and for writes just before the write and just before
  success is returned. A symbolic link or junction elsewhere that points
  into the ledger is not detected. An alias created after the last check is
  not detected either: the operation can still succeed, and a later
  operation refuses only if its own checks see the alias (measured for a
  hardlink: a later read refuses). The checks are not atomic (section 11).

**Provenance.** Results produced through injected runners are labelled
`unit_mock`, and `require_genuine_provenance` refuses them with
`provenance_not_genuine`. A provenance field is a consistency check, not
proof.

**Not shown.** A good signature is not permission for any effect. The
admission module must still compare live facts immediately before the
effect, and it does not exist yet.

## 6. Receipt (delivered; a genuine receipt is always refused)

`tools/manual_bridge_merge_receipt.py` defines the receipt contract and an
append-only writer. A receipt labelled genuine is always refused with
`integration_prerequisites_unverified`, because these five prerequisites
remain UNKNOWN:

- `bridge_event_provenance`
- `identity_registry_binding`
- `author_lineage`
- `rco_veto_admission`
- `github_merge_authenticity`

Tests write only receipts labelled `synthetic_unit_mock`.

- The approval roles are `rco`, `build_lead` and `build_tools`.
- Every approval, and a recorded autonomous refusal, must be timestamped
  before GitHub's `mergedAt` second.
- A receipt directory without its completion marker is an unaccepted failure
  artifact. A failed write can also leave a marker that verifies, for example
  after a close failure, an interruption or a refused completion check once
  the marker was written. Every directory left by a failed write must be
  reconciled with the verifier. The caller never retries, deletes or
  overwrites it; the writer refuses another directory for the same PR and
  12-character head prefix, but nothing prevents deletion.

No receipt exists for any real merge, and no retroactive receipt will be
made for past merges.

## 7. Approvals: three real approvers

Under the target design, an admitted merge needs three distinct identities,
all at the exact head:

- the Lead's `build_consensus_pass`;
- Tools' `build_consensus_pass`;
- one `rco_pass` from a recognized RCO (`claude-rco-1` or `claude-rco-2`) who
  is not an author of the PR.

The following never fill these slots:

- an author waiver;
- the operator's signature, which never fills the RCO slot and is never
  recorded as `RCO_PASS`;
- outside reviewers (Grok, or the GPT reviewer on G1759).

A recognized-RCO veto always wins (`CLAUDE.md` Rule 9a).

**Controls (DN-A).** Control admission is not implemented. The initial
contract is an audit proposal awaiting the Lead's reconciliation. It proposes:

- The negative-control scope is the conservative union of the legacy readers:
  - the canonical task;
  - the author slash/hyphen aliases;
  - the PR keys `pr`, `pr_number`, `pull_request` and
    `pull_request_number`;
  - the PR pattern in task ids.
- The positive scope is strict: exact task, exact head, exact status.
- Typed canonical RCO findings cannot be neutralized by status or prose.
- Unknown or unclassifiable controls refuse.
- No release path exists in the first version, so an ordinary PASS clears
  nothing.

The unchanged aggregate gates remain separate mandatory gates. They are
necessary, never sufficient.

## 8. Operator key guidance (operator only)

**Who handles the key.**

- Only the operator creates the signing key, on a device the operator
  controls.
- No agent creates, reads, stores, copies or asks for the private key or its
  passphrase.
- No agent emergency bypass exists or will be invented.

**Key type.**

- Preferred: a FIDO2 security-key-backed ed25519-sk key (OpenSSH type
  `sk-ssh-ed25519@openssh.com`), with touch required.
- Fallback, only if the operator has no FIDO2 authenticator: an `ssh-ed25519`
  key protected by a strong passphrase. Never use an empty passphrase. Never
  load the key into ssh-agent or any other agent. Type the passphrase only
  when signing.

**Storage.** Keep the private key outside every repository checkout, working
tree, audit folder and shared or synced folder. Never commit it and never
paste it into the bridge or a chat.

**Delivery.** The operator gives the Lead exactly one public key line: the
contents of the public key file. The Lead builds the single anchor line from
the principal, the namespace option and that public key.

**Fingerprint check before signing.** Compute the fingerprint of the public
key file with OpenSSH's fingerprint listing (`ssh-keygen -l`), never on the
private key. Compare it with:

- the `key_fingerprint` field of the statement;
- the fingerprint derived from the base anchor (section 9).

Sign only if all three are identical.

**Limit: verify cannot tell how the key is protected.** `ssh-keygen -Y verify`
proves only that the signature was made with the anchored key.

- It cannot tell a passphrase-protected `ssh-ed25519` key from an unprotected
  one.
- Whether the FIDO user-presence (touch) flag is enforced for
  `sk-ssh-ed25519@openssh.com` signatures is UNKNOWN on this host.
- The anchor shows the key type, but not how the key is protected.

**Signing.** Signing instructions are deliberately not in this document. They
belong to the exact-head signing request, which may be prepared only at plan
step 5.3.

**Rotation or loss.** Key rotation or loss is a later, separate operator
decision.

## 9. Verifying a signature (public side)

These steps use public material only.

1. **Base.** Take `base_sha` from the statement and confirm that it is the
   PR's actual base.
2. **Anchor blob id.** Run
   `git rev-parse <base_sha>:ops/security/manual-merge.allowed_signers`. The
   result must equal `allowed_signers_blob_sha` in the statement.
3. **Anchor copy.** Copy the anchor out of the base commit with
   `git show <base_sha>:ops/security/manual-merge.allowed_signers` into a
   temporary file outside the repository.
   - Never use a copy from the PR head or from a working tree.
   - Use a shell that writes bytes unchanged, such as Git Bash or cmd.exe.
     Windows PowerShell's `>` operator re-encodes text.
   - `git hash-object <anchor copy>` must equal the blob id from step 2.
4. **Verify.** Run
   `ssh-keygen -Y verify -f <anchor copy> -I operator@waggledance -n waggledance-manual-merge-a -s <statement.sig> < <statement.json>`.
5. **Output.** Accept only the exact Good line from section 5, with the
   expected fingerprint.
6. **Live facts.** A good signature alone is not permission. Also check:
   - the head equals the live PR head;
   - the statement has not expired;
   - the exact paths and the diff digest match the section 5a facts for the
     statement's base and head (`git diff-tree` raw bytes, hashed with
     SHA-256);
   - the diff does not touch the anchor;
   - the nonce has never been used.

## 10. Bootstrap (this PR)

**No self-receipt.**

- This bootstrap PR cannot produce a receipt for itself.
- A signature is not a merge.
- No agent runs merge, ready or undraft on it.

**Human merge only.** The operator alone merges it, after signing its exact
head. Signing is possible only once plan step 5.3 holds:

- the Lead's and Tools' build consensus at the exact head;
- an `RCO_PASS` from `claude-rco-1` (or the backup RCO) at the exact head;
- green CI;
- no veto.

**Head movement.** If the head moves after signing, the signature is void.

**Autonomous refusal.**

- Record that the autonomous merge gate refused this PR.
- If the current gate emits no refusal event, record that it is absent.
- Do not build a refusal or a MAGMA receipt for it.

**Forbidden.** `--admin`, `--no-verify` and force-push are forbidden.

**After the merge.**

- The route is not in use in the fleet until a separate operator release
  decision.
- No Rule 10 cutover.
- No Rule 9b activation.
- No new RCO identity.

## 11. Known limits

- **Shared GitHub account.** The shared GitHub account can still merge
  outside this route. The route provides evidence and a receipt; it does not
  prevent such merges.
- **Key protection.** Verify cannot tell how the key is protected
  (section 8).
- **Local ledger.** The nonce ledger is local state, and deleting it is not
  cryptographically prevented.
  - Windows has no `openat`, so an ancestor swap that races the alias checks
    is UNKNOWN.
  - Host ACLs are UNKNOWN.
  - Crash durability is UNKNOWN.
  - POSIX behaviour is UNKNOWN.
- **Merge and receipt are not atomic.** A merge whose receipt fails leaves an
  indeterminate state. That state is reconciled, never retried.
- **Legacy gates at base.** The legacy gates at base still carry known
  permissive cases (cause-B C1; the fix PR #1762 is unmerged). This is why the
  aggregate gates count as necessary but never sufficient.

## 12. Open items (not decided here)

- Lead reconciliation of the DN-A initial contract and its open questions.
- The admission, preview and execute module, and its tests (G2). It must
  re-run the section 5a check immediately before any effect.
- The trust anchor, which needs the operator's public line.
- Real validators for the five integration prerequisites.
- Positive SSH proof and live proofs, both NOT_RUN.
- A separate operator release decision before any fleet use.
