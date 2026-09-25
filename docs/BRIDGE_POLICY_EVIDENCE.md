# Policy evidence sources

`tools/bridge_policy_evidence.py` reads declared files under one allowlisted
root, hashes the bytes it actually read, and emits the evidence structure that
`tools/bridge_policy_epochs.py` consumes.

It exists because the epoch builder validates a digest's *shape*, which is not
the same as having verified the content. This module closes that gap for the
file-backed classes.

## Hash integrity is not content authenticity

This distinction is the point of the module, so it is worth stating flatly.

**Hash integrity** — "this digest is of the bytes that were on disk at read
time, read from a descriptor we validated, and the file did not change identity
or content under us." That is what this module establishes, and it reports it
as `hash_integrity`.

**Content authenticity** — "these bytes are the legitimate, intended content."
That requires a signature or a trusted publisher. Neither exists here, so
`content_authenticity` is `"unverified"` on **every** path, including a fully
successful read. A caller that needs authenticity must get it somewhere else,
and must not infer it from a green `hash_integrity`.

### What the read actually checks

Three independent checks, because each catches something the others miss:

1. **Open-handle final path.** After opening, the module asks the OS what it
   actually opened — `GetFinalPathNameByHandleW` on Windows,
   `/proc/self/fd/N` on Linux — and refuses if that path is not inside the
   root. A pre-open path check cannot do this, because the swap can happen
   between the check and the open. If the platform cannot answer, the source is
   **refused** (`open_handle_path_validation_unsupported`) rather than assumed
   good.
2. **`st_ino` / `st_dev` before and after**, proving the descriptor still
   refers to the same filesystem object. An earlier version compared only size
   and mtime, so a substitution that matched both would have passed.
3. **Size, mtime and bytes-read**, which catch in-place mutation.

**Honest limit:** this *detects* interference; it does not *prevent* it. A
privileged writer that restored every observable would defeat all three. That
is another reason authenticity is never claimed. If the design ever needs a
genuinely safe reader, that is a separate mechanism and a separate decision —
this module should not be read as providing one.

## The root

Must be an existing absolute directory, with no reparse point anywhere in its
chain, **on an allowlisted drive**. The allowlist defaults to `C` and is
overridable per call via `allowed_drives`. (An earlier version documented
"persistent C" while the code accepted any drive letter; the allowlist is now
the code, not the prose.)

## What it refuses

Every source is a relative POSIX path under the root. Refused, with a reason,
never normalised into something acceptable:

| Refusal | Reason emitted |
| --- | --- |
| leading/trailing whitespace on the path | `source_path_has_surrounding_whitespace` |
| `../outside` or any `..` component | `path_traversal_forbidden` |
| `/etc/passwd`, `C:/Windows/...` | `absolute_source_path_forbidden` |
| `file.json:stream` | `alternate_data_stream_forbidden` |
| `PROGRA~1`, `trailing.`, `trailing ` | `ambiguous_windows_alias` |
| symlink or reparse point in any component | `reparse_point_in_path` |
| absent file | `source_missing` |
| directory, device, pipe | `source_is_not_a_regular_file` |
| over the per-file or remaining-total bound | `source_exceeds_size_bound` |
| opened handle resolves outside the root | `open_handle_escapes_root` |
| platform cannot validate the handle's path | `open_handle_path_validation_unsupported` |
| inode/device changed during the read | `source_identity_changed_during_read` |
| size or mtime moved during the read | `source_changed_during_read` |

**Diagnostics are fixed strings.** They never interpolate a manifest key or a
path component, because those are caller-controlled and would otherwise be
echoed into every log and bridge reply carrying a reason.

**Budgets are charged before the read**, not after, and each read is capped to
the remaining total allowance. Limits: 4 MiB per file, 64 documents, 32 MiB
total, shared across all classes. (An earlier version checked after hashing, so
the advertised total could be exceeded by a whole document.)

## What it will not fabricate

* A class with no manifest entry, or whose sources are unusable, is
  **unavailable** with reasons and **omitted** from the emitted evidence. Never
  a placeholder digest. The epoch builder then marks it unknown and admission
  parks — so an unevidenced world stops by construction.
* **`native` is not file backed at all.** Native identity comes from the live
  binding; the manifest rejects a `native` key outright.
* **A model label is not qualification evidence.** `{"model": "some-name"}` is
  refused; a qualification entry needs a hashed report plus non-empty
  `evidence_ids` and `verdicts`.
* A profile entry needs a hashed record plus `profile_id` and
  `authorization_ref`.

## Policy evidence must name its domain

`policy.domain` is mandatory. "Policy" is not one corpus:

* **`configs/policy/**`** is *deployment / runtime* policy — constitution and
  per-deployment profile policies (apiary, cottage, factory, home).
* **Bridge governance policy** is a different corpus (`CLAUDE.md`, the
  consensus-approval contract, the charter).

They must never be conflated, because a deployment document must not be able to
look like authorization for a bridge decision. The domain label travels with the
emitted evidence so a consumer can tell which corpus it is holding.

The integration binds the domain into `bridge_policy_epochs._policy_material`.
Identical document references and digests in different domains produce different
policy epochs. Missing domains remain unknown. This prevents cache identity
collisions; a domain label does not authenticate or authorize the policy.

## Which sources actually exist today

Surveyed by running `inspect_sources` against the real tree, not by assertion.
**Note the domain:** what exists is *deployment* policy. This survey found **no
bridge governance policy corpus wired as evidence**, and nothing here is bridge
switch authorization.

| Class | Status | Detail |
| --- | --- | --- |
| `policy` (deployment domain) | **available** | `configs/policy/default_constitution.yaml` plus `configs/policy/profile_policies/{apiary,cottage,factory,home}.yaml` — 5 documents, 8 938 bytes |
| `policy` (bridge governance domain) | **not wired** | The governance corpus exists as documents but is not declared as evidence anywhere, and this module will not adopt it implicitly |
| `catalog` | **unavailable** | No model/effort/tool profile catalog exists. The `profile_policies/*.yaml` files are *deployment* policy profiles and are not that catalog; pointing at them would be mislabelling. |
| `qualification` | **unavailable** | `tools/bridge_model_qualification.py` exists but no stored report does. |
| `profile` | **unavailable** | No authorized-profile record exists. |
| `native` | n/a | Live binding, by design. |

So a real run today emits one class and three unavailables, the epoch snapshot
has one epoch and four unknowns, and admission parks. That is correct for the
current state of the repository, and it is why this module does not make
admission usable on its own.

## Usage

```python
from tools.bridge_policy_evidence import build_snapshot

built = build_snapshot(root=REPO_ROOT, manifest=MANIFEST, binding=binding)
built["source"]["unavailable"]   # what could not be evidenced, and why
built["snapshot"]["epochs"]      # feed to bridge_task_admission.admit
```

`inspect_sources()` answers "what can this machine back today?" without
building a snapshot.

## Not wired

Nothing imports this module. It performs no write, touches no collector,
service or scheduler, and the emitted evidence carries no permission.
