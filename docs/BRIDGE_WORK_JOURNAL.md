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

Append is the only operation that can CREATE a database. Two operations
write one: append, and `publish_pending` recovery, which allocates a fresh
sequence and republishes a committed-but-unpublished state. Status writes
nothing and opens no database at all.

```powershell
python tools/bridge_work_journal.py append --database .codex-audit/work-journal.sqlite3 --input receipts.json
```

It validates the complete input before opening SQLite and appends the whole
batch in one transaction. A conflict or interruption rolls the batch back.

Status never opens the database at all. It reads the newest published
snapshot, verifies its full digest and the sequence embedded in the image,
and loads those bytes into an in-memory copy:

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

**Snapshots are bound to one RESOLVED database path.** They were originally
keyed by directory, so asking about a missing `b.sqlite3` returned
`a.sqlite3`'s receipts from the shared folder. The key is now a digest of
`Path.resolve()` of the database path, so `../` segments, symlinks and — for a
file that exists — casing all collapse to one journal. Lexical `absolute()` did
not, and a status call through an aliased spelling reported `unavailable` for
receipts genuinely committed and published through the canonical one.

Resolving does **not** weaken the no-live-database-read promise: it asks the
filesystem about *path components*, stating and reading links. It never opens
the database, never reads a byte of it, and cannot create it.

Three residuals are stated rather than implied away:

* **Hardlinks are not unified.** Two hardlinks to one inode are two genuine
  real paths, so they key apart. There is no path-only way to detect this, and
  detecting it would mean opening the file, which this design forbids.
* **A database that does not exist yet is case-sensitive in its spelling,**
  because `resolve()` cannot canonicalise a missing component; both spellings
  converge once the file exists. The key hashes the path *string*, while
  `Path.__eq__` is case-insensitive on Windows, so the regression tests compare
  derived keys and never `Path` objects.
* **The key is no longer a pure function of the string.** It depends on
  filesystem state, so a symlink or junction created or removed later can change
  it for an unchanged spelling and appear to move the journal.

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
if the current sequence is already published it reports `already_published` —
verified by loading that snapshot, not by finding its name in the listing.

If the snapshot for the current sequence exists but **fails verification**,
recovery repairs the journal by republishing the committed database at a fresh
sequence, and says so with `recovered_from: corrupt_snapshot:<seq>`. It does not
raise: this is the entry point whose job is to restore a readable journal, and
raising here would make the one operation meant to recover the one that cannot.
Corruption is still never silent — the reader refuses an unverifiable snapshot,
and the repair is reported rather than passing as an ordinary publish.

### Bounds, and bounded retention

* At most `MAX_SNAPSHOT_ENTRIES` directory entries are examined; more is a
  refusal, so a flooded directory cannot turn a read into an unbounded job. The
  bound counts **every** entry, matching or not, because what is bounded is the
  walk, not the snapshot count. See *The snapshot directory is a trusted
  directory* below — this bound is also a stated trust assumption, not only a
  cost guard.
* Retention has two levels. `MAX_SNAPSHOT_RETAINED` is a **soft prune target**:
  after each successful publish, snapshots *strictly older* than the one just
  published are deleted oldest-first until the target is met.
  `SNAPSHOT_RETENTION_SLACK` is the tolerated overshoot, and the hard stop is
  `MAX_SNAPSHOT_RETAINED + SNAPSHOT_RETENTION_SLACK`, derived rather than
  configured separately so the two can never be set into an inverted order.
  Only at the hard stop does an append prune pre-emptively, and only if that
  still frees nothing is the append refused **before** its commit.
* Retention deletes **only this journal's own cache snapshots**: files inside
  this journal's snapshot directory whose names match the snapshot pattern and
  parse cleanly. The database and its receipts are never candidates, nor is any
  other file in that directory, nor an identically-named file outside it. The
  prune iterates the parsed scan rather than a glob, and a test plants decoys —
  including a perfectly valid snapshot name beside the database — to keep it so.

#### The tradeoff, stated plainly

The previous design deleted nothing. That is the safest possible answer to "can
retention destroy a reader's view" — and it was the wrong one, because it
converted a full directory into **permanent silent lag**: appends kept
committing while publication reported `publication_pending` forever, so the
reader fell further behind with every write and nothing in the system ever
recovered. Unbounded correctness of the snapshot set was bought with unbounded
staleness of the view, which is the failure the journal exists to prevent.

Bounded deletion is safe here for a structural reason, not a hopeful one: a
snapshot is only ever a candidate once a **newer published snapshot exists**, so
the state a reader would load next is never the state being removed. A reader
already holding an older file is protected by the filesystem, which refuses the
delete while the handle is open (measured on Windows); the prune tolerates that
refusal, steps past it to the next candidate, and retries the stuck file on a
later pass. The pre-commit gate passes the *current* newest sequence, so it
cannot delete the reader's present view on behalf of an append that has not
happened yet — and a refused append leaves that view intact.

What is given up: snapshot history is no longer permanent, so a superseded
snapshot cannot be used as an audit trail of past states. The journal's
authority was always the committed database, not the snapshot set, so nothing
that was ever authoritative is lost — but anyone who wanted to diff old
snapshots must copy them out, because retention will reclaim them.

The alternative to both — refusing the append at the bound with no deletion at
all — remains as the hard stop, for the case where pruning genuinely cannot
free anything. It is a visible, pre-commit refusal, never a quiet lag.

### The snapshot directory is a trusted directory

This is an assumption, so it is written down rather than left implicit.

The snapshot directory is **journal-owned and trusted**. Anyone able to create
arbitrary files inside it can already delete published snapshots, which is a
trust tier comparable to write access to the database file itself.

If that directory is flooded past `MAX_SNAPSHOT_ENTRIES`, **all three
operations refuse** — `status`, `append`, and `publish_pending` — even when a
genuine, valid, loadable snapshot is still present. There is **no in-module
recovery path**: `publish_pending`, the module's own recovery entry point,
refuses for exactly the same reason. Recovery is an **operator** action: remove
the foreign entries.

That is a deliberate choice between two bad options. The module will not delete
files it does not own, and that refusal is the same guarantee that makes bounded
snapshot retention safe. Widening the scan, or auto-deleting clutter, would
trade a documented denial of service for an undocumented ability to destroy
data belonging to someone else.

### Append outcomes

`append_receipts` returns `publication` alongside the append result, so
committed-but-unpublished is distinguishable from committed-and-published:

| `publication` | meaning |
| --- | --- |
| `published` | committed, and the snapshot for `snapshot_seq` exists |
| `publication_pending` | **committed**, but not published; `publication_reason` says why |

`retention_warning` appears beside either outcome when the post-publish prune
could not run — `prune_refused:<reason>` for a refused directory scan,
`prune_failed:<OSError type>` for an I/O failure. It qualifies the housekeeping,
never the publication: the snapshot named by `snapshot_seq` is on disk and
loadable, and only the reclaiming of older ones did not happen.

Nothing after the commit raises, **and that includes the cleanup**. A publication
problem — a refused sequence, a directory error — becomes `publication_pending`
with a reason, because an exception there would hide a durable commit behind what
looks like a failed append. The prune that follows a successful publish reads the
snapshot directory and so can fail the same way; it reports `retention_warning`
rather than throwing over a publication that already succeeded. The snapshot
directory is resolved once, *before* the transaction, so the post-commit stretch
derives nothing and a test asserts that against the source. An image too large to
publish is refused *before* the commit instead, so a journal is never committed
into a state the reader could never see.

### Replaying a publication

Publishing the same sequence twice is idempotent, but idempotence is decided on
the stored **bytes**, not on the stored **name**. A file's name carries the
digest it claims to have; corruption does not rename it. So a replay loads the
existing snapshot and verifies its digest and embedded sequence before reporting
success. A same-sequence file whose content differs, and one whose content no
longer verifies, are both refused — and because that refusal happens inside the
post-commit guard, the caller sees `publication_pending` with a reason rather
than an exception.

### Schema versions

`_initialise` creates the tables and the metadata row if they are absent,
then inspects the stored version **before changing it**. The `INSERT OR
IGNORE` cannot overwrite an existing row, so a database claiming version
999 keeps it and is refused. Only an
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
* **Superseded snapshots are reclaimed, so old states are not archived.** The
  committed database remains the authority; copy a snapshot out if you need it.
* **The hard stop can still refuse an append** when pruning frees nothing —
  visibly and before the commit, never as silent lag.
* **Content addressing is not authenticity.** It detects corruption; it does not
  establish that the publisher was entitled to publish.
* **Hardlinks key apart,** and a database that does not exist yet is
  case-sensitive in its spelling; see *Snapshots are bound to one resolved
  database path*.
* **The journal key depends on filesystem state.** A symlink or junction created
  or removed later can change it for an unchanged path spelling.
* **A flooded snapshot directory denies all three operations and needs an
  operator.** There is no in-module recovery; the journal will not delete files
  it does not own.
