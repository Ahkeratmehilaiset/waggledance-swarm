# Bridge lane profiles

Status: **PR-1 of lane profile switching: catalog and validator only.** Nothing in
this PR launches, stops or reconfigures a lane, and nothing reads or writes runtime
state. The feature is specified on bridge task `lane-profile-switching`, spec v3.

## Terms

A **profile** is the capacity layer's profile: provider, account pool, model and
effort, plus that profile's quota buckets. The term is reused from
`tools/bridge_capacity_advisor.py`; there are no "weights".

A **lane** is one bridge agent: codex-lead-1, codex-tools-1, claude-rco-1,
claude-rco-2 or fable-5. grok-scout-1 is deferred, because it has no launcher path.

## The catalog

`configs/lane_profile_catalog.json`, schema `wd.lane-profile-catalog.v1`, is a
versioned wrapper. Its validator, `tools/lane_profile_catalog.py`, covers every field:

- `capacity_policy`: the advisor's `wd.bridge-capacity-policy.v1` object, carried
  whole and validated by the advisor's own `_validate_policy`. The wrapper also
  refuses any key the advisor does not read, at the root, per profile and per agent
  binding, because the advisor does not reject unknown keys. Nothing may ride inside
  the policy that one consumer honours and another ignores. The advisor and the
  planner consume this same validated object.
- `providers`: the effort enum per provider. Every profile's effort must be in it.
- `lanes`: per lane, `allowed_profiles` is ordered **strongest -> weakest**. That
  order is the only definition of *raise* and *lower*. It must be an in-order subset
  of the lane's advisor allowlist, so it can narrow the list but never widen or
  reorder it. The other fields are `floor` (the lowest index Lead may choose without
  an operator ack), `default` (never below the floor), `max_relaunches_per_hour`,
  `cooldown_seconds` and `reviewer`, which must be true exactly for claude-rco-1 and
  claude-rco-2.
- `fleet`: `mode` (`shadow` | `approve` | `auto`), `max_relaunches_per_hour_total`
  (no lane budget may exceed it), `verify_timeout_seconds`, and the `shadow_exit` and
  `approve_exit` criteria.
- `catalog_ref`, `operator_signature`: the operator signs by replacing
  `operator_signature` in a reviewed PR. The validator records the signature; it does
  not verify it.

`load_catalog(path)` reads exactly that one file, bounded to 256 KiB, UTF-8 JSON
with no NaN or Infinity. It returns the catalog and the sha256 of its bytes. Every
later receipt carries that hash.

## Transition classification

`classify_transition(catalog, lane, current, target)` is pure. Its verdicts:

| verdict | when |
|---|---|
| `same` | target == current |
| `raise` | target is stronger than current and within the floor |
| `lower` | target is weaker, still within the floor, and the lane is not a reviewer |
| `park` + `operator_ack_required` | target not allowed; target below floor; current unknown or not in the catalog; reviewer lowering; lane not in catalog |

A reviewer lane never lowers without an operator ack, even inside its floor, because
a reviewed party must not weaken its reviewer. An unknown current profile always
parks, since a raise from an unknown state cannot be proven to be a raise.

## Shipped defaults

The defaults are the profiles measured on 2026-09-26: rco-1 and rco-2 on
claude-sonnet-5 xhigh, Lead on gpt-5.6-sol medium, Tools on gpt-5.6-terra medium,
and fable-5 on claude-opus-5-5 medium. Each lane gets one stronger option. Each
lane's floor is its current profile, so the shipped catalog permits raise-or-same
only. The mode is `shadow`, and the signature reads `UNSIGNED-DEFAULT`.

## Governance

The catalog, its floors and any change to `fleet.mode` are (a)-class, needing an
explicit operator signature and never standing consensus. So is every later PR that
wires this onto a runtime path. `approve` stays unavailable until
configs/bridge_identity_registry.json binds an operator identity. On a single-user
host the agents run as the same Windows user, so that binding is policy plus audit,
not cryptographic isolation.

## Non-goals

No token reservation, no fleet task scheduler, no account or quota-pool changes, no
cross-session context sharing, no change to merge-gate authority, and no attaching
to or steering a peer session's turns.

## What this PR does not prove

The validator proves that a catalog is well formed and internally consistent. It does
not prove the following:
- that a profile's model is available, or that its quota has headroom;
- that the operator actually signed;
- that any relaunch would succeed.

Those need the later deliverables: the D2 record, D3 binding, D4 verified relaunch
and D5 planner, and live evidence in `approve` mode.
