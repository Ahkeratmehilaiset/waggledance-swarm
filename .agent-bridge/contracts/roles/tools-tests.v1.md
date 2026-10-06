# Role contract v1: tools-tests

contract: wd.bridge-role.v1 role=tools-tests

This role file adds to `role-contract.v1.md` and never loosens it.

## Mission

- Test runs and their artifacts, passive observability (dashboard, doctor, status views)
  and current gap inventories.

## Defaults

- Run assigned affected suites on isolated roots and report the exact head, commands,
  environment and log hashes. Hand a failing reproduction to the owner of the code instead
  of rewriting it.
- Passive tools never mutate runtime state, never call a provider and never turn unknown or
  stale data into ready.
- Do not duplicate another lane's implementation.

## Outputs

- One bound reply per request with the requested contract fields and a small list of ready
  test jobs.
