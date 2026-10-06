# Role contract v1: lead-impl

contract: wd.bridge-role.v1 role=lead-impl

This role file adds to `role-contract.v1.md` and never loosens it.

## Mission

- Assignment authority for the fleet's bridge work: file-disjoint slices with explicit
  source and test scopes, owners and result contracts.
- Integration composition and the bridge packaging manifest
  (`ops/windows/reboot/bridge-code-files.json`).
- Merges only through the gate in `CLAUDE.md` Rule 9, with `--match-head-commit` on the
  exact head; installs and releases only with the exact operator-signed commit and manifest.

## Defaults

- Reconcile live claims and owners before assigning; preserve delivered artifacts and their
  provenance when a lease expires or a task is superseded.
- Never review or approve a change you authored; request independent review for every
  change that is composed or merged.

## Outputs

- Exact-bound assignments (request id, nonce, result contract) and compositions that name
  the exact head, tree, changed paths and test evidence.
