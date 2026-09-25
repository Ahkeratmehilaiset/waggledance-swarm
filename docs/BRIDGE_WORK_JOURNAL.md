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

### How the zero-write guarantee is achieved

An earlier version made that claim while opening the database with
`?mode=ro`, which creates `-shm` and `-wal` for a WAL-mode database — so the
claim was false exactly where it mattered. SQLite now never opens the file at
all:

1. **Classify from the header.** The first 100 bytes are read directly; header
   byte 18 gives the journal mode. `PRAGMA journal_mode` cannot be used for
   this, because by the time it answers, the sidecars already exist. A file
   that is not a database is refused here, after 100 bytes, rather than being
   copied into memory first.
2. **Refuse unmerged state.** A non-empty `-wal` may hold committed frames that
   are not in the main file, and a hot `-journal` means the main file may be
   mid-transaction. Either is **refused**. This is the case `immutable=1` alone
   would get wrong: it would ignore those frames and return a shorter history
   while looking successful, which is worse than refusing because the caller
   cannot tell that receipts were dropped.
3. **Snapshot into memory.** The image is read once, bounded, with the file
   identity (`st_ino`/`st_dev`/size/mtime) compared across the read; a file
   that moves mid-read is refused rather than reported.
4. **Load the copy.** The in-memory copy — never the file — is relabelled from
   WAL to rollback in its header, because SQLite cannot deserialize a WAL image
   into an in-memory database. This is sound *only* because step 2 already
   established the main image is the complete committed database.
5. **Validate coherence.** `PRAGMA quick_check` must return `ok`. A trailing
   damaged page can otherwise let the query return a plausible short history.

The result is that `status` performs `stat` and a read-only `open`, and nothing
else, on every path including every error path. The regression tests assert the
directory is byte-identical before and after, for success, WAL, hot journal,
corruption and truncation alike.

### Unknown is not absent

Only `FileNotFoundError` means a sidecar is absent. A permission denial, an IO
error or any other `OSError` while observing `-wal`/`-journal` leaves their
state **unknown**, and unknown is refused.

An earlier version caught `OSError` broadly and continued, so a stat that was
merely *denied* looked exactly like a database with no unmerged frames.
Reproduced: with the `-wal` stat denied, a database holding 8 272 bytes of
genuinely unmerged WAL was read anyway and reported one row, silently dropping
the committed frame.

### Concurrency limits — what is detected, and what is not

The sidecars are observed **twice**, once before the snapshot and once after,
and the read is refused if either observation shows unmerged content or if the
two observations differ at all — including a size-preserving touch.

That is **detection, not exclusion**, and the difference matters:

* **Detected:** a writer that creates or grows a `-wal`, or leaves a hot
  `-journal`, at any point that either observation can see. Reproduced before
  the fix: a writer committing into a fresh `-wal` during the snapshot produced
  a successful read whose result silently lacked the committed frame.
* **NOT detected:** a writer that creates, commits, checkpoints and *removes*
  a `-wal` entirely between the two observations. Both observations would show
  the same absent state and the snapshot could still be torn.
* **NOT attempted:** reading a database that has unmerged state. That is
  refused outright, so `status` is unavailable while a writer holds
  uncheckpointed frames.

So `status` does **not** claim a coherent snapshot against a live writer. It
claims that no sidecar change was observed across the read, and refuses
otherwise. A guarantee against a concurrent writer would need a different
storage design — coordinated locking or a writer that publishes immutable
snapshots — and is deliberately not claimed here.

The `journal.coverage_note` and nested ledger notes are intentional: an empty
or partial local journal cannot prove that all accepted work is represented.
