# Bridge Event Log Rotation Executor V1

`tools/bridge_event_log_rotation.py` is the first mutating step after
`tools/plan_bridge_events_rotation.py`.

The default mode is dry-run. With `--apply`, the tool writes only:

- the planned archive prefix under `.agent-bridge/shared/archive/`
- a JSON receipt next to that archive

It does not rewrite or truncate `.agent-bridge/shared/events.jsonl`.

This keeps the unsafe part of full compaction out of this slice. A future
truncate/rewrite step still needs all of these controls before it can be
enabled:

- explicit operator-gated apply flag
- append-race proof or cooperative writer lock
- archive verified before any events rewrite
- gate-reader window longer than the maximum open-PR lifetime
- head-bound bridge receipt after successful rewrite

The staged archive receipt records the source, archive, recent, and
reconstructed SHA-256 digests from the read-only plan. The live source file is
read again after staging; the report only stays `ok` when the source is still
identical or has only grown by append.
