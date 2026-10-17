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
  path: the commit pointer, replaced last and atomically, whose `last_ok` names the committed cycle's files under
  `cycles/<cycle dir>/` and binds the capture, context, state and board text by sha256 and the publication by its
  snapshot sha256 (the receipt is named, not hashed).

## The loop

1. Read `latest.json`, the board its `last_ok.board_file` names, and your own lane and PR lines.
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

* **No content guard:** the capture round's `capture.json`, `detail-manifest.txt`, `gh-pr-list.json`, files cache
  and `context.json`; the producer's `detail-manifest.txt` (a ref, a row sha256 and a source word per row, built
  from the guarded capture's verified rows); the capacity lens's raw `capacity-status.json`.
* **Only a derived object:** the capture round writes `gh-open-status.json` after the guard of the status object
  derived from it, which does not cover the response's unprojected fields.
* **Copies without a guard of their own:** the capture round's `--reuse-context` copies `gh-pr-list.json`,
  `context.json`, `gh-open-status.json` and `observed-status.json` from an earlier capture as they are (whether
  that capture guarded them is not checked). The producer copies nothing: `latest.json` names the shadow run's
  files in the committed cycle's dir.
* **Guarded whole before the write:** `observed-status.json` and `capture-record.json` (its keys and strings, not
  the files it lists) of the capture round; the lens views and texts (`capacity-lens.json`, `lens-limits.txt`,
  `overlap-lens.json`, `lens-overlap.<lane>.txt`); the shadow run's `reflected.publication.json`, `delta.jsonl`,
  `board.txt` and `receipt.json` (the receipt after the other three are written); the producer's `capture.json`,
  `state.json` and `context.json` (each also read back the way its consumer reads it before the first write), its
  files-cache views, its `live-log.jsonl` records, `last-refusal.json` (fixed fields of a guarded record) and
  `latest.json`. The lane view writes no file; it returns a guarded view and a guarded text.
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
  starts; `--window` is held to 1 to 99500 lines and `--keep` to at least 1, both at parse time, before the code
  tree's imports and any write or process start. A delta reads each fresh row from the last 600 lines plus the
  rows its listing read after the cursor (the listing's `snapshot_events`), so every listed row lies within the
  read (RCO1's L1: a cursor that has fallen further behind is read, not refused); a delta needing more than 100000
  lines refuses the cycle (`delta_beyond_tail_cap`). The delta's detail reads run in a pool of `--workers`: when
  the first refused one in listed order is taken up, the reads not yet started are cancelled and the running ones
  finish. Only a fresh start (no `latest.json`) seeds: it lists the last `--window` lines with `-ContinuityTail`
  and reads each listed row from the last `--window` + 500 lines. `capture/capture_round.py` holds its reader the
  same way for the two calls it makes. A `--seed-from` file is read whole once, after the code tree's imports and
  before any write or process start, and refused whole with a fixed reason and exit 2 with the usage when it
  cannot be read (`seed_unreadable`), is not UTF-8 JSON (a leading BOM, nesting beyond the parser's depth and an
  int literal beyond the int digit limit included: `seed_unparseable`), is not an object whose events are a list
  of objects (`seed_shape_unexpected`) or holds a row whose canonical ref cannot be built
  (`seed_ref_unverifiable`); `capture/capture_round.py` refuses it the same way after its count check, with its
  own refusal. At a fresh start a seed row is reused only when its own canonical ref equals a listed ref, so the
  file's schema and other keys are not read, and a seed file changed after the start is not read again.
* **Restart.** At start the producer reads `<live-dir>/latest.json` whole once, right after the `--seed-from`
  file and before any write or process start. A missing file is a fresh start. A file it cannot read
  (`latest_unreadable`), one that is not ASCII JSON of the kind it writes (a BOM, nesting beyond the parser's
  depth, an int literal beyond the int digit limit, a NaN or an infinite number: `latest_unparseable`) and one
  that is not an object of its own schema (`wd.bridge-mind-shadow-latest.v2`) whose `last_ok` is one it commits
  (one cycle dir, the generation of its state, that cycle's capture, state, publication, board and receipt files
  under their own names, and the capture, state and snapshot sha256: `latest_shape_unexpected`) are refused
  whole, with exit 2 and the usage. A `latest.json` of another tool or of the earlier window/cursor producer is
  refused this way, so the producer neither writes nor prunes in that live dir, and so is a live dir holding an
  entry named `state`, the earlier producer's state dir (`live_dir_legacy_state`): fresh production state only,
  nothing read or migrated. A live dir of another tool without a `latest.json` or a `state` entry is not
  recognized.
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
  name instead, so a name never repeats and the names sort in the order the dirs were made. Such a name runs ahead
  of the clock until the clock passes it; the cycle's `started_utc` in the log keeps the clock's time. Measured
  with a stand-in clock. An entry whose name does not read as a UTC second is not counted, and an entry another
  tool makes in `cycles` is outside this (on a case-insensitive file system, a name that differs from the new one
  only in case still fails its mkdir). A dir that cannot be made refuses the cycle (`cycle_dir_unavailable`).
* **State.** The committed state is one versioned envelope, `cycles/<cycle dir>/state.json`: the window rows, the
  sha256 of their bytes, the cursor, the bridge log identity the cursor names (file identity and log generation)
  and the generation (1 after a fresh start, one more per commit). `latest.json`'s `last_ok` names it with its
  sha256 and generation, so the state a cycle consumes is the one the last committed pointer binds (one success
  epoch). A cycle reads it whole once, through the core's sharing retry, before any write: bytes that do not hash
  to the named sha256, do not parse as one strict JSON object, or do not verify (another generation, a cursor
  that does not name the identity the state holds, a row not of the producer's row shape or not its own event's,
  a repeated ref, another window sha256) refuse the cycle with `committed_state_unverified`, and nothing of it is
  reused. The order of the rows is not verified.
* **No reseed (B3).** A reader refusal of the listing or of a detail read, a new cursor that names no bridge log
  identity (`compact_cursor_invalid`) or, in a delta, another one than the committed state's
  (`state_source_changed`: a rotated or replaced log), and any read the producer cannot verify refuse the cycle
  with a fixed reason: there is no reseed and no skip, and the next cycle starts from the same committed state.
  The reader's refusals: a reader that times out or raises OSError (`compact_reader_timed_out`,
  `compact_reader_not_started`), exits non-zero (`compact_reader_failed`) or prints output that is not UTF-8, not
  JSON or not one object (`compact_output_unparseable`); a view without its schema, a string cursor or, in a
  delta, its row count (`compact_view_shape`); events that are not a list of objects each with a scalar ref
  (`compact_events_shape`); a listed ref outside the reader calls (`reader_option_invalid`, before a process
  starts); and a detail report that is not the detail of that ref (`detail_ref_mismatch`) or whose canonical ref
  cannot be built (`detail_ref_unverifiable`). Any other error refuses the cycle under its type name. A cycle
  refused after its dir is made leaves that dir, which `latest.json` never names. So an event the reader refuses
  in every read (RCO1's L2) now holds the producer at the committed state: every later cycle refuses with the
  same fixed reason, visibly in the log, and recovering from such a stall is an explicit operator step, not code
  here (B3's liveness limit: fresh state and an initial 30-minute canary only). Measured with a fake reader.
* **Commit (B2).** Each cycle builds one candidate dir. Every payload (capture, detail manifest, context, state)
  is guarded and read back the way its consumer reads it before the first write (the capture by the shadow's
  `read_capture`, the state by the producer's own check, the context by the workspace's `read_context`; a
  guarded word refuses with `output_guard`, anything else with `candidate_invalid`), and each is created
  exclusively (`candidate_unwritable`). The shadow run must have read the very capture and context written
  (`candidate_changed`); it gets the previous publication only when that snapshot, verified by the core's
  `read_snapshot`, hashes to `last_ok.snapshot_sha256` (else none, recorded `previous_identity_mismatch`; B4).
  Then retention runs, the cycle's record with status `prepared` (no success claim) is appended to
  `live-log.jsonl`, flushed and synced, and `latest.json` is replaced LAST: a unique temporary file in the same
  dir, flushed and synced, then `os.replace` through the core's sharing retry (only the Windows sharing window, a
  PermissionError, with a bounded backoff), the own temporary removed when the replace did not happen.
  `latest.json` changes only on a commit, so a refused or interrupted cycle never advances the consumed state or
  loses a row, and a log record never commits a cycle: a process ended between the record and the pointer leaves
  the prepared record as that cycle's last, with `latest.json` still naming the earlier epoch.
* **Retention (B4).** After a candidate is complete and before its record and pointer, the capture, state and
  detail manifest of each cycle dir older than the last `--keep` names are removed, never those of `last_ok`'s
  cycle or of the candidate (the prior recoverable epoch and the one about to be committed). Only the producer's
  own paths are touched: a name that reads as a cycle dir and is a real directory, each file a regular file; a
  link or other reparse point, at either level, is never followed or removed and is counted as skipped. A listing
  or removal that fails refuses the cycle before its commit (`prune_failed`), so `latest.json` and `last_ok`
  stay. Each cycle's `context.json`, the shadow run's files under its `run/` and the cycle dirs themselves are not
  removed; a refused candidate's dir is pruned like any old one.
* **Refusals.** A refused cycle's record (its fixed reason, no success field) is appended to `live-log.jsonl`;
  when the log cannot be written, that record's fixed fields replace `<live-dir>/last-refusal.json` atomically;
  when neither can be written, the producer writes one fixed STOP line to stderr and exits 3, committing nothing
  further. A log or pointer write that fails after the prepared record appends a refused record after it
  (`log_unwritable`, `latest_unwritable`). A record whose own fields the privacy guard refuses is replaced by its
  fixed fields with the reason `output_guard`.
* **GitHub reads.** Its read-only GitHub metadata calls are the only network touch (Lead's B6: bounded read-only
  metadata, a failure is UNKNOWN): one `gh pr list` (number, state, head, branch) and, for open PRs,
  `gh pr view --json headRefOid,commits`, at most 40 views and 150 seconds per cycle and 60 seconds per call. A
  view is kept in `<live-dir>/files-cache` per PR and head as typed metadata only (the head and the lane-shaped
  commit author names; no message, body or path). gh failing, timing out or not starting, an output of another
  shape, or a view past the cycle's bounds leaves the context, or that PR's authors, UNKNOWN; it never refuses
  the cycle. A cached view that does not validate leaves the whole context UNKNOWN (no partial reuse), and the
  context is written only when the workspace's own `read_context` accepts it. No classifier, charter or gate code
  is imported or run: every `path_class` is null and the charter is not loaded, so a computed class stays
  UNKNOWN. A view cache write that fails refuses the cycle (`view_cache_unwritable`). `gh pr list` stops at 400
  rows, so when it reaches them each PR of the cycle's own bridge snapshot that it left out is read with one
  `gh pr view` of the same four fields, within the same bounds; a PR still missing, or a snapshot the core refuses,
  leaves the whole context UNKNOWN, which suppresses nothing (the board then reads bridge facts only and can still
  suggest a vote for a PR merged outside the bridge view): a bounded backfill, not a fail-closed suppression.
* **Import.** The program runs only as a script (`__main__`): importing the module (the package's import smoke)
  defines its constants, classes and functions and runs, imports, writes and starts nothing. A fresh-interpreter
  test checks this, and a guarded run of the script refuses every gate, classifier, charter and taxonomy module
  by name and by gate word.
* **Experiment code.** Experiment code stays offline and reads only the copied capture files.

## JOIN card limits (RCO2 R1 and R2, disclosed for this release)

The operator preview of the JOIN card delivery covers only new waits on a PR with exactly one reported empty
identity slot: an abstained or refused PR is outside its ledger and one with two or more empty slots gives no line
either, so a preview without lines is no proof that all work is complete. The lane view gives each recognized RCO
its own slot and each empty slot a line that says "the vote is still required", but a PR's class stays UNKNOWN here
(no classifier or charter runs) and an UNKNOWN class does not establish that a second RCO slot is required: such a
line for the fourth identity slot (the second RCO) is an observation, not a required gate slot. Nothing in the view,
the card, the delivery or the preview is a required-policy, last-required-vote or utility claim.
