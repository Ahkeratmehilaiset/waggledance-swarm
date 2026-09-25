# Accepted-work journal

`tools/bridge_work_journal.py` is a deliberately dormant, standard-library
SQLite journal for explicit accepted-work receipts. It is not a collector,
scheduler, runtime integration, or source of inferred usage or money.

## Receipt model

The append document has this strict shape:

```json
{
  "schema": "wd.work-journal-input.v1",
  "accepted_work": [
    {
      "contract_id": "contract-a",
      "revision": "1",
      "artifact_id": "artifact-a",
      "evaluation_id": "evaluation-a",
      "state": "accepted",
      "observed_at": "2026-09-25T08:00:00Z"
    }
  ]
}
```

The immutable receipt identity is
`contract_id/revision/artifact_id/evaluation_id/state`. Its timestamp and all
other fields are immutable payload. Re-appending byte-equivalent normalized
content is idempotent. Supplying the same identity with a different payload is
refused rather than silently changing history. A later acceptance after a
reopen therefore needs a new artifact or evaluation identity.

The state timeline is determined by `observed_at`, not append order. A late
arrival of an older acceptance cannot resurrect a revision already reopened
at a later timestamp.

## Explicit operations

Append is the only operation that can create or write a database:

```powershell
python tools/bridge_work_journal.py append --database .codex-audit/work-journal.sqlite3 --input receipts.json
```

It validates the complete input before opening SQLite and appends the whole
batch in one transaction. A conflict or interruption rolls the batch back.

Status only reads an existing database with SQLite `mode=ro`:

```powershell
python tools/bridge_work_journal.py status --database .codex-audit/work-journal.sqlite3
```

`status` never creates a database, parent directory, WAL/rollback sidecar, or
schema—also not for a missing or malformed database. Its output nests the
deterministic report from `tools.bridge_work_ledger`; no money, usage, source
authority, or coverage outside explicitly stored receipt rows is invented.

### Status reads published snapshots, never the database

`append` publishes an **immutable snapshot** of the state it commits. `status`
reads the newest published snapshot and **never opens the database at all**.

Snapshots live in `snapshots/<journal key>/` beside the database, where the key
is a digest of that database's absolute path, and are named
`snap-<12-digit sequence>-<full sha256 of the image>.journal`. A file is never
modified or replaced once named, so a reader holding one has a coherent image
no matter what a writer does next.

**Snapshots are bound to one database.** They were originally keyed by
directory, so asking about a missing `b.sqlite3` returned `a.sqlite3`'s
receipts from the shared folder. The key binds a snapshot set to the database it
describes, and the reader derives it from the path it was given — no database
read is needed to establish the binding.

**The filename is an untrusted label.** The reader verifies the full sha256
*and* the sequence embedded inside the image. Renaming a valid snapshot from
sequence 1 to 99 preserves the digest, so the digest alone cannot say which
state an image represents.

**Publication is coupled to the commit, not to a later read.** Inside the same
write transaction that appends the receipts, the sequence is allocated from
`journal_metadata.snapshot_seq` and the image is captured with
`Connection.serialize()`. An in-transaction serialize includes the pending rows
(verified). Publication then happens **only after the commit succeeds**, because
an image captured from a transaction that later rolls back contains rows that
were never committed (also verified). A fresh post-commit read is not used: it
could contain another writer's rows under a sequence we allocated.

Publishing writes a temp file in the same directory, fsyncs, and renames it to a
name that does not yet exist. It never replaces a live file, because
`os.replace` onto an existing target fails on Windows while a reader holds it
open (measured).

### What `status` guarantees, exactly

* It performs `scandir`, `stat` and read-only `open`, and nothing else, on every
  path including every error path.
* It verifies the **full sha256** of the content against the digest in the name.
* A duplicate sequence, a filename sequence that disagrees with the embedded
  one, an unparseable image, a failed `quick_check`, an unsupported schema or an
  oversize file are all **refusals**.
* Reads are bounded by size at both the `stat` and the read, so a file that
  grows between the two cannot be slurped in full.
* With **no published snapshot** the state is `unavailable` and `receipt_rows`
  is `None`. There is no live-database fallback, and this is deliberately not
  reported as an empty journal: a journal whose state is unknown and a journal
  with no receipts are different things.

### What `status` does NOT tell you

`latest_committed_state` is **always `"unknown"`**. Because the reader never
opens the database, it cannot know whether a commit exists that has not been
published. Concretely: after a crash between commit and publish, the newest
snapshot is **stale and is not current authority**. `snapshot_seq` and
`snapshot_as_of` describe the snapshot, not the database, and `snapshot_as_of`
is derived from the receipts themselves rather than from any clock.

`publish_pending()` recovers that case. It appends nothing, so it cannot
duplicate a receipt, and it allocates a fresh sequence with a fresh
in-transaction image rather than re-reading under the old one. It is idempotent:
if the current sequence is already published it reports `already_published`.

### Bounds, and why nothing is deleted

* At most `MAX_SNAPSHOT_ENTRIES` directory entries are examined; more is a
  refusal, so a flooded directory cannot turn a read into an unbounded job.
* At `MAX_SNAPSHOT_RETAINED` snapshots, publication reports
  `publication_pending` with `retention_bound_reached` and the append stays
  committed. **Nothing is ever deleted automatically.** Silently removing an
  immutable artefact is a worse failure than a visible refusal to publish, and
  an operator can tell the difference between "full" and "quietly discarded".

### Append outcomes

`append_receipts` returns `publication` alongside the append result, so
committed-but-unpublished is distinguishable from committed-and-published:

| `publication` | meaning |
| --- | --- |
| `published` | committed, and the snapshot for `snapshot_seq` exists |
| `publication_pending` | **committed**, but not published; `publication_reason` says why |

Nothing after the commit raises. A publication problem — a refused sequence, a
directory error — becomes `publication_pending` with a reason, because an
exception there would hide a durable commit behind what looks like a failed
append. An image too large to publish is refused *before* the commit instead, so
a journal is never committed into a state the reader could never see.

### Schema versions

`_initialise` inspects the stored version **before** mutating anything. Only an
explicit version 1 is migrated to 2; any other unrecognised version is refused
and left untouched. An earlier version wrote the current number
unconditionally, silently downgrading a database that claimed version 999.

A `publication_pending` result is not a failed append. The receipts are durable;
only the reader's view is behind, and `publish_pending()` closes the gap.

### The live-database reading path is gone

`status` used to read the database directly, with machinery for WAL sidecars,
hot journals and mid-read disappearance. That code has been **removed**, not
merely bypassed: `_read_receipts`, `_classify_database`, the sidecar helpers,
`_snapshot_bytes` and `_as_rollback_image` no longer exist. The tests that
asserted their behaviour are retired, and a regression asserts the functions are
absent so the claim cannot quietly become false again.

### Remaining limitations

* **Readers can lag a committed append across a crash.** Inherent to publishing
  separately from committing.
* **Every append copies the whole image.** Fine for a dormant receipt journal;
  a blocker if it grows, at which point the shape must change rather than be
  tuned.
* **Retention can be reached and block publication** rather than silently
  reclaiming space.
* **Content addressing is not authenticity.** It detects corruption; it does not
  establish that the publisher was entitled to publish.
