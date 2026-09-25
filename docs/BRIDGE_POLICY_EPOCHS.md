# Policy epoch snapshots

`tools/bridge_policy_epochs.py` derives the five epoch values that
`tools/bridge_task_admission.py` consumes. It is observation only: it reads
evidence a caller supplies, hashes it, and returns a snapshot. It mutates
nothing, starts nothing, and carries no permission to change a profile.

## Why this module exists

The admission controller caches a decision against a key made of the task
binding plus five epochs. That cache is only safe if an epoch changes exactly
when the thing it describes changes — no sooner and no later. Two failure
modes would each break it:

* an epoch derived from a clock or a fresh identifier would change constantly,
  so every check would look like a new world and the cache would never hold;
* an epoch that a caller could simply *assert* would let a caller claim a
  world it has not evidenced.

So every epoch here is a digest over declared evidence, and there is no code
path that accepts an epoch value as input.

## The five epochs and what each needs

| Epoch | Evidence |
| --- | --- |
| `policy_epoch` | `evidence.policy.documents[]`, each `{ref, sha256}`, deduplicated by `ref` |
| `catalog_epoch` | `evidence.catalog` = `{ref, sha256}` |
| `qualification_epoch` | `evidence.qualification` = `{ref, sha256, evidence_ids[], verdicts{}}` |
| `profile_epoch` | `evidence.profile` = `{ref, sha256, profile_id, authorization_ref}` |
| `native_epoch` | `binding`: `agent_id`, `session_id`, `native_thread_id`, and a valid process epoch (`native_pid` + `native_process_started_at`) |

`epoch_inputs()` returns the same table at runtime.

A `sha256` must be 64 hex characters. Case is accepted either way and
normalised to lowercase, so a manifest that stores uppercase digests and a
tool that stores lowercase ones agree.

Each value is namespaced — `catalog_epoch:<32 hex>` — so two epochs computed
over coincidentally equal material can never collide.

## Rules

**Derived, never accepted.** No caller can supply an epoch. This is the
property that makes the snapshot evidence rather than assertion.

**No clock, no randomness.** The module imports no time or random source, and
a test asserts that statically. A snapshot taken twice over unchanged evidence
is byte-identical.

**Missing evidence stays missing.** An epoch that cannot be derived is *absent*
from `epochs` and recorded in `unknown` with the reasons. It is never
defaulted, back-filled, or given a placeholder. Because `admit()` requires all
five epoch fields, an incomplete snapshot parks admission by construction —
nobody has to remember to check `complete` first.

**A model label authenticates nothing.** Qualification evidence consisting of a
model name is refused. A label is not an attestation of provider identity and
not a measurement of quota. The snapshot always carries
`provider_authenticated: false` and `quota_verified: false`, on every path,
including a complete one.

**The snapshot permits nothing.** `execution_allowed` and `switch_permitted`
are `false` on every path.

### One nuance worth challenging

`native_process_started_at` is a timestamp, and this module is supposed to
avoid wall-clock input. The distinction is that the field is not a reading of
"now": it is the start stamp that turns a reusable PID into a specific process
instance, and `bridge_capacity_recovery._valid_process_epoch` already treats it
that way. It changes when the process changes and at no other time, which is
exactly what an epoch needs. A PID without it yields
`native_identity_missing:process_epoch` rather than an epoch.

## Using it with admission

```python
from tools.bridge_policy_epochs import snapshot, to_admission_epochs
from tools.bridge_task_admission import admit

snap = snapshot(evidence, binding=binding)
request = {"binding": binding, "epochs": to_admission_epochs(snap), ...}
verdict = admit(request)
```

`to_admission_epochs()` omits unknown epochs rather than emitting empty
strings, so the verdict is `PARK` with an `epoch_unknown:<name>` reason
whenever evidence is incomplete.

## Unresolved production wiring

This is the part a reader should not skim.

* **Nothing produces this evidence yet.** There is no durable source in the
  repository for the policy document set, the profile catalog, the
  qualification report or the profile record. The builder is therefore inert
  in production: with no evidence it returns five unknowns, admission parks,
  and nothing happens. That is the safe direction, but it means the module is
  not yet useful without a separate, separately reviewed change that emits the
  evidence.
* **The evidence digests are trusted as given.** The builder checks that a
  digest is well formed; it does not re-hash the referenced file. Whoever emits
  the evidence is responsible for the digest matching the content, and that
  producer does not exist yet either.
* **`verdicts` shape is minimal on purpose.** It is validated as a non-empty
  mapping of text to text. Tightening it to a known verdict vocabulary needs
  the qualification corpus to exist first, and inventing that vocabulary now
  would be fabricating a contract.
* **Not wired, not integrated, not reviewed for production use.** Nothing
  imports this module.
