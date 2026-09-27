# WD-Supervisor and unreadable (opaque) hosts

`WD-Supervisor` runs `ops/windows/reboot/wd_supervisor.ps1` as the interactive
user at `RunLevel=Limited`. From that token, the command line of an elevated
`powershell.exe` or `pwsh.exe` is unreadable. The supervisor calls such a
process an *opaque host*. An opaque host could be a Tools consumer wrapper
that the supervisor cannot see, so it never counts as proof that the wrapper is
absent.

## When an opaque host blocks

An opaque host blocks Tools reconciliation, and the run reports a `CONFLICT`
and exits 1, except in three cases:

1. **The readiness record names exactly one opaque host.** The run reports
   `UNVERIFIABLE`: the wrapper is alive but its command line is unreadable.
2. **The recorded wrapper is gone** (native terminal only). No readable process
   claims the role and the readiness owner is provably gone. The run reports
   `IGNORED` and may relaunch the wrapper.
3. **One ready native-terminal wrapper holds the role.** All of these must hold:
   - exactly one readable wrapper;
   - it is the exact generation;
   - its readiness record is bound to its own PID, start time and generation;
   - there is no legacy consumer.

   The run reports `UNVERIFIED n unreadable host(s) beside ready consumer-loop`.
   It launches, stops and replaces nothing. The ownership of the opaque hosts
   is not verified, and the report does not say it is.

Case 3 is safe because of the lifetime lock. A native-terminal wrapper opens
`.wd-turn-codex-tools-1.lock` with `FileShare.None` before it writes readiness,
and holds it until it exits. So any hidden duplicate fails at the lock before
its native start. `tests/tools/test_wd_supervisor_opaque_process.py` pins both
that ordering and the refusal. A headless wrapper has no such lock, so for it a
healthy wrapper beside an opaque host stays a `CONFLICT`.

## Why case 3 exists

`start-wd-all.ps1 -Auto` runs elevated, because it needs to change Task
Scheduler. The processes it starts directly are therefore opaque to the
supervisor:
- the four interactive lanes, through Windows Terminal;
- the bridge conversation viewer;
- the restore process itself.

The watchers and Tools are started through the Limited task and stay readable.
The restore ends with a scheduled-path health proof: it runs the task once and
requires a result of 0.

The opaque-host block came in with #1731. Before case 3, a restore therefore
met a readable, ready Tools wrapper beside its own opaque processes, which is a
`CONFLICT` and exit 1. So from #1731 on, the health proof could not pass. Any
`-Auto` restore that reached it left `WD-Supervisor` Disabled, as happened on
2026-09-27 at 19:41Z.

The same `CONFLICT` had also been in every 30-minute run while elevated lanes
were up: 39 of 48 runs on 2026-09-26 and 38 of 40 on 2026-09-27, per
`wd_supervisor.log`.
