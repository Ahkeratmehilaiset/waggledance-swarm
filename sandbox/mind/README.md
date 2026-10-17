# Bridge Mind sandbox (code only)

Operator directive 2026-10-13, relayed by RCO1: the shadow mode becomes a real sandbox. Every lane connects to the
Mind and makes it faster and more correct in light rounds. Only a measured improvement goes to `main`, once,
through the normal PR path.

This branch family holds code only. Data (bridge captures, snapshots, run outputs) lives on the operator-chosen
sandbox data disk and never goes to git. The repository is public.

## Layout

* `sandbox/mind-main` (fable-5 merges lane fixes here without a review round). It is stacked on the Mind
  heads: slice 1 `6d60d69e` (workspace context and shadow pipeline), core and pager `5292f625` (Tools), board
  `d5ae03f2` and the X2 board fixture patch.
* `sandbox/mind-<lane>-*`: one branch family per lane, stacked on `sandbox/mind-main`.
* Data root: `<sandbox root>/<lane>/`. Each lane writes only its own directory. The live outputs of fable-5's
  producer are under `<sandbox root>/fable-5/.codex-audit/mind-live/`, and `latest.json` is the one standard
  path, written last and atomically.

## The loop

1. Read `latest.json`, `latest-board.txt` and your own lane and PR lines.
2. Report wrong, missing or stale facts as `type=message` on `fable-5/bridge-mind-shadow-live-20261013`, with the
   ref and the capture sha. No request id is needed.
3. Fix on your sandbox branch. Copy that cycle's `capture.json` and `context.json` into your own data dir, then
   rerun with your code:
   `python -B -m tools.wd_bridge_mind_shadow --capture C --context X --audit-root <yours>/.codex-audit --out-dir <new>`.
   Post one line: what changed, the metric before and after, the capture sha.
4. Grok joins once per round, one consultation at a time, in rotation. The prompt is under 12 KB and is built
   from the round summary and metrics only; it is scanned before launch, and the prompt and report hashes are
   posted.
5. The final measured version goes once through PR, CI, both RCOs, build votes and the class check.

Metrics: time to answer what a PR needs and what my next action is, bytes and events read per decision, wrong
facts per hour, rebuild and query speed.

## Hard rules (always)

* No import or call of gate code: verify_bridge_consensus, check_bridge_changes_requested,
  check_rco_pass_present, idle_consensus_auto_merge, the rule-12 evaluator, the merge and receipt executors.
* No bridge event writes from sandbox code. Agents report themselves.
* Writes only under the sandbox data root. No network, provider call, model install or download in experiment
  code. The capture and lens writers take their directories from their callers and do not check the root
  themselves; the shadow run checks only that its files stay inside its `.codex-audit` root.
* No sandbox data in git. A decoded privacy guard runs on every output, with one known exception, open for Lead's
  decision: what the capture path stores as read (next section).
* The Mind is advisory. Before any vote or pass, check with the canonical tools.

## What the capture path stores without a content guard (known exception, open for Lead's decision)

The guard checks the objects the capture path derives and every output built from them, not every byte the path
stores as read. The rows come from Tools' static map of the writers (10-16: capture round, lenses, lane view,
workspace, shadow run) and from my own reading of the producer, the board and the delta, which that map did not
cover. Everything stays under the sandbox data root, where the callers place it.

* **No content guard:** the producer's `state/window.jsonl` and `state/cursor.txt`; `capture.json`,
  `detail-manifest.txt`, `gh-pr-list.json`, the files cache and `context.json` (producer and capture round); the
  capacity lens's raw `capacity-status.json`.
* **Only a derived object:** the capture round writes `gh-open-status.json` after the guard of the status object
  derived from it, which does not cover the response's unprojected fields.
* **Copies without a guard of their own:** the capture round's `--reuse-context` copies `gh-pr-list.json`,
  `context.json`, `gh-open-status.json` and `observed-status.json` from an earlier capture as they are (whether
  that capture guarded them is not checked); the producer copies this cycle's guarded `board.txt`,
  `reflected.publication.json` and `receipt.json` of the shadow run to `latest-board.txt`,
  `latest.publication.json` and `latest-receipt.json` byte for byte.
* **Guarded whole before the write:** `observed-status.json` and `capture-record.json` (its keys and strings, not
  the files it lists) of the capture round; the lens views and texts (`capacity-lens.json`, `lens-limits.txt`,
  `overlap-lens.json`, `lens-overlap.<lane>.txt`); the shadow run's `reflected.publication.json`, `delta.jsonl`,
  `board.txt` and `receipt.json` (the receipt after the other three are written); the producer's `live-log.jsonl`
  line and `latest.json`. The lane view writes no file; it returns a guarded view and a guarded text.
* **A later refusal removes nothing:** the workspace's context read refuses a context whose branch name, declared
  class-hit paths or commit authors hold a guarded word, but the stored `context.json` stays (the overlap lens
  reads it with its own shape check, refusing before any write a context it cannot read whole, and guards only
  its own view), and a refusal after earlier writes does not roll those writes back.

## The capture producer

`sandbox/mind/mind_capture_producer.py` is the live capture loop and the subject of RCO1's boundary review. It is
not the only component with input I/O: `capture/capture_round.py` reads the bridge the same way and GitHub with
more read-only calls for one tournament round, `capture/capacity_lens.py` runs the installed capacity observer's
read-only status script, and the overlap lens, the input freezer, the tournament driver and the build receipt
run read-only local `git` commands.

* **Bridge reads.** It reads the bridge only through the inherited pinned `Read-AgentBridge.ps1 -Compact` path:
  refs after a position cursor, then `-Compact -EventId` for each full row, and each canonical ref must match. A
  non-Compact read (`-Raw` included) drains the accepted queue under the global publication fence, so the
  producer never uses one. The reader options must be one of the three calls it makes, slot by slot
  (`-AfterEvent` and a bc1 position cursor of at most 2048 characters, or `-ContinuityTail` and a count within the
  compact tool's own tail bound, 1 to 100000 lines, then optionally `-EventId` and a 64-hex ref), or no process
  starts; `--window` is held to 1 to 99500 lines (a seed reads each listed row from the last `--window` + 500
  lines) and `--keep` to at least 1, both at parse time, before the code tree's imports and any write or process
  start (a window above the bound is refused also when every listed row would be reused: fail closed).
  `capture/capture_round.py` holds its reader the same way for the two calls it makes. A `--seed-from` file is
  read whole once, after the code tree's imports and before any write or process start, and refused whole with a
  fixed reason and exit 2 with the usage when it cannot be read (`seed_unreadable`), is not UTF-8 JSON (a leading
  BOM, nesting beyond the parser's depth and an int literal beyond the int digit limit included:
  `seed_unparseable`), is not an object whose events are a list of objects (`seed_shape_unexpected`) or holds a
  row whose canonical ref cannot be built (`seed_ref_unverifiable`); `capture/capture_round.py` refuses it the
  same way after its count check, with its own refusal. A seed row is reused only when its own canonical ref
  equals a listed ref, so the file's schema and other keys are not read, and a seed file changed after the start
  is not read again.
* **Restart.** At start the producer reads `<live-dir>/latest.json` whole once, right after the `--seed-from`
  file and before any write or process start. A missing file is a first start. A file it cannot read
  (`latest_unreadable`), one that is not ASCII JSON of the kind it writes (a BOM, nesting beyond the parser's
  depth, an int literal beyond the int digit limit, a NaN or an infinite number: `latest_unparseable`) and one
  that is not an object of its own schema whose `last_ok` is null or an object naming one cycle dir
  (`latest_shape_unexpected`) are refused whole, with exit 2 and the usage. A `latest.json` of another tool is
  refused this way (live_mind's included), so the producer neither writes nor prunes in that tool's live dir;
  a live dir of another tool without a `latest.json` is not recognized. The `last_ok` names the previous
  publication for the next shadow run (none when that cycle's `run/reflected.publication.json` is gone), and its
  other keys are not checked; each later `latest.json` names it as read until a cycle succeeds, except the privacy
  guard's refusal form, which names none, so a start after that form has no previous publication.
* **One producer per live dir.** Right after the live dir is made and before any other write or process start,
  the producer locks `<live-dir>/producer.lock` without waiting and holds the lock for its life: on Windows one
  byte at 1 GiB, past the end of the empty file (Windows locks are mandatory, so a lock of the first byte would
  refuse every read or copy of the file while a producer runs), elsewhere the whole file with an exclusive
  `flock`. A live dir whose lock another process holds (`live_dir_in_use`) and a lock file it cannot open or lock
  otherwise, a directory in its place included (`live_dir_unlockable`), are refused with exit 2 and the usage,
  before anything else is written there or any process starts. The descriptor is not inheritable, so no reader
  process holds the lock, and the OS releases it after the process ends, a crash included. The lock file is
  created when missing, never truncated or written, and not removed. The lock is only as strong as the file
  system's own locking, and the `flock` branch does not run on this Windows host (stand-ins check its call).
* **Cycle dirs.** Each cycle's dir under `<live-dir>/cycles` is named by the UTC second its cycle starts
  (`YYYYMMDDTHHMMSSZ`). When that name is not after the newest name there that reads as a UTC second (the clock
  set back, or a restart within the second of the last cycle), the dir is named by the second after that newest
  name instead, so a name never repeats and the names sort in the order the dirs were made: old captures beyond
  `--keep` are pruned in that order. Such a name runs ahead of the clock until the clock passes it; the cycle's
  `started_utc` in the log keeps the clock's time. Measured with a stand-in clock. An entry whose name does not
  read as a UTC second is not counted, and an entry another tool makes in `cycles` is outside this (on a
  case-insensitive file system, a name that differs from the new one only in case still fails its mkdir). A
  refused cycle's dir counts toward `--keep` like any other, so `--keep` refused cycles in a row prune the capture
  that `latest.json`'s `last_ok` names.
* **State.** The window and its cursor persist under `<live-dir>/state`. A cycle continues after the cursor only
  when every persisted row still verifies against its own canonical ref and row sha256 and no ref repeats (the order
  of the rows is not verified). A missing, undecodable (a line nested beyond the parser's depth included) or
  unverified state, an empty window or a delta the reader refuses reseeds from `-Compact -ContinuityTail`. The
  reader refuses the delta's listing when the cursor is outside the reader calls, the compact tool exits non-zero,
  or its output is an object without the view schema or a string cursor, and one of its detail reads when the
  listed ref is outside the reader calls, the tool exits non-zero, or the report is an object that is not the
  detail of that ref. A detail read of the delta takes only the last 600
  lines of the log, so a listed ref none of whose rows lies in those lines is refused (RCO1's L1: a cursor that has
  fallen further behind); the reseed's detail reads take the last `--window` + 500 lines. Only a cursor outside the
  reader calls is refused before a process starts; any other refused listing has already run one reader process.
  The delta's detail reads run in a pool of `--workers`: when the first refused one in listed order is taken up, the
  reads not yet started are cancelled and the running ones finish. The reseed is one list read and then one
  `-EventId` read for each distinct listed ref that is not reused (a verified persisted row after a refused delta,
  or a `--seed-from` row), and the read record's `reseed_after` names the refused read (`listing` or `detail`) and
  its fixed reason; it names no ref, and it records neither a gap nor that the reseed covered the earlier history.
  The read record counts the final mode only: `new_rows` the distinct listed refs not already in the window (after
  a reseed every distinct listed ref, the reused rows included), `detail_calls` its `-EventId` reads and `stats`
  its listing's; the reads of an abandoned delta are neither counted nor reused. Nothing of an unverified state is
  reused. A reader that times out or raises OSError (`compact_reader_timed_out`, `compact_reader_not_started`:
  mostly its start failing, but an OSError later in the run is named so too), an output that is not UTF-8, not
  JSON (nesting beyond the parser's depth included) or not one object (`compact_output_unparseable`), a view whose
  events are not a list of objects each with a scalar ref (`compact_events_shape`) and a detail event whose
  canonical ref cannot be built, for a non-finite number or a lone surrogate (`detail_ref_unverifiable`), refuse
  the cycle with that fixed reason and no reseed, in a delta as in a seed; any other error refuses it under its
  type name, also without a reseed (a read error of a state file, for example), and so does a refused reseed.
  Measured with a fake reader. Whether any of these should reseed instead is the stall-or-reseed choice, which
  the producer does not make; an empty object or string as events is refused like any other events that are not
  a list. Each of these is a read before the window and cursor writes, so those two files are left as they were
  and the
  next cycle starts from the same state; but the cycle directory has been made before the read, and after the
  refusal old cycle captures beyond `--keep` are pruned and the cycle's log line and `latest.json` are written. A
  failed window or cursor write, and an error in a later step of the cycle, are outside this. Events that are an
  empty list read as no new rows, and a listed ref that is a number, null, a boolean or a string not of the ref
  shape is refused by the detail read's option check before a process starts, which reseeds. So an event the
  reader refuses in every read (RCO1's L2) refuses each cycle until
  it is no longer among the last `--window` lines (3000 by default) that the reseed lists. Its aging out is not a
  recovery: those refused cycles and their reseeds never capture the event, and no record of the producer names
  it (an earlier capture or a `--seed-from` row is outside this statement); whether such an event should stall
  the capture or be recorded as a gap is Lead's decision.
* **GitHub reads.** Its read-only GitHub metadata calls (`gh pr list` and `gh pr view` for files and commits)
  are the only network touch. Whether they are allowed is a carve-out that Lead decides.
* **Experiment code.** Experiment code stays offline and reads only the copied capture files.
