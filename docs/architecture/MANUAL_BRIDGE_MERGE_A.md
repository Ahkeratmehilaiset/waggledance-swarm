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

| Component | Path | State at head `43af852a` |
|---|---|---|
| Statement contract, trust-anchor loader, SSH verify, nonce ledger | `tools/manual_bridge_merge_statement.py` | Delivered. Unit-tested with injected runners only. |
| Receipt contract and writer | `tools/manual_bridge_merge_receipt.py` | Delivered. A genuine receipt is always refused. |
| Tests for those two modules | `tests/tools/test_manual_bridge_merge_statement.py`, `tests/tools/test_manual_bridge_merge_receipt.py` | Delivered. The evidence class is `unit_mock`. |
| Admission, preview and execute | `tools/manual_bridge_merge.py` | NOT delivered. |
| Merge-module tests | (planned with the admission module) | NOT delivered. |
| Trust anchor | `ops/security/manual-merge.allowed_signers` | NOT delivered. Absent at main `c5f7c933` and at head `43af852a`, so the anchor is UNKNOWN. |
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
- the live diff does not touch the anchor;
- the statement has not expired (`statement_expired`).

**One-time use.** `verify_statement` is preview-safe and consumes no nonce.
One-time use is enforced by the nonce ledger, and only the admission module
(not delivered) would drive it.

- Ledger states: `reserved`, `refused_before_effect`, `merge_started`,
  `executed`, `indeterminate`, `reconciled_merged`, `reconciled_not_merged`.
- The ledger refuses link, junction and hardlink aliases.

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
  artifact. It must be reconciled, never retried.

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
   - the exact paths and the diff digest match;
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
- The admission, preview and execute module, and its tests.
- The trust anchor, which needs the operator's public line.
- Real validators for the five integration prerequisites.
- Positive SSH proof and live proofs, both NOT_RUN.
- A separate operator release decision before any fleet use.
