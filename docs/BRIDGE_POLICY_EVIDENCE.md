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
time, and the file did not move under us while we read it." That is what this
module establishes, and it reports it as `hash_integrity`.

**Content authenticity** — "these bytes are the legitimate, intended content."
That requires a signature or a trusted publisher. Neither exists here, so
`content_authenticity` is `"unverified"` on **every** path, including a fully
successful read. A caller that needs authenticity must get it somewhere else,
and must not infer it from a green `hash_integrity`.

The change-detection is honest about its own limits too: it is a stat-read-stat
sandwich over the **open descriptor**, not the path, so a name swap mid-read is
caught. A writer that restored size and mtime exactly would still defeat it.
It detects a concurrent writer; it does not prevent one.

## What it refuses

Every source is a relative POSIX path under the root. Refused, with a reason,
never normalised into something acceptable:

| Refusal | Reason emitted |
| --- | --- |
| `../outside` or any `..` component | `path_traversal_forbidden` |
| `/etc/passwd`, `C:/Windows/...` | `absolute_source_path_forbidden` |
| `file.json:stream` | `alternate_data_stream_forbidden` |
| `PROGRA~1`, `trailing.`, `trailing ` | `ambiguous_windows_alias:<part>` |
| symlink or reparse point in any component | `reparse_point_in_path:<name>` |
| absent file | `source_missing` |
| directory, device, pipe | `source_is_not_a_regular_file` |
| larger than 4 MiB | `source_exceeds_size_bound` |
| size or mtime moved between the two stats | `source_changed_during_read` |

Bounds: 4 MiB per file, 64 documents, 32 MiB total.

The root itself must be an existing absolute drive-qualified directory with no
reparse point anywhere in its chain.

## What it will not fabricate

* A class with no manifest entry, or whose sources are unusable, is
  **unavailable** with reasons and **omitted** from the emitted evidence. It is
  never filled with a placeholder digest. The epoch builder then marks it
  unknown and admission parks — so an unevidenced world stops by construction,
  not by anyone remembering to check.
* **`native` is not file backed at all.** Native identity comes from the live
  binding. Reading it from disk would be inventing it, so the manifest rejects
  a `native` key outright.
* **A model label is not qualification evidence.** `{"model": "some-name"}` is
  refused. A qualification entry needs a hashed report plus non-empty
  `evidence_ids` and `verdicts`.
* A profile entry needs a hashed record plus `profile_id` and
  `authorization_ref`; it will not synthesise a current profile.

## Which sources actually exist today

Surveyed at `b2250ba7` by running `inspect_sources` against the real tree, not
by assertion:

| Class | Status | Detail |
| --- | --- | --- |
| `policy` | **available** | `configs/policy/default_constitution.yaml` plus `configs/policy/profile_policies/{apiary,cottage,factory,home}.yaml` — 5 documents, 8 938 bytes |
| `catalog` | **unavailable** | There is no model/effort/tool profile catalog in the repository. The `profile_policies/*.yaml` files are *deployment* policy profiles and are not that catalog; pointing at them would be mislabelling. |
| `qualification` | **unavailable** | `tools/bridge_model_qualification.py` exists but no stored report does. There is nothing to hash. |
| `profile` | **unavailable** | No authorized-profile record exists. |
| `native` | n/a | Live binding, by design. |

So today a real run emits one class and three unavailables, the epoch snapshot
has one epoch and four unknowns, and admission parks. That is the correct
outcome for the current state of the repository, and it is why this module does
not yet make admission usable on its own.

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
