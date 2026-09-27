# Scheduled-task console containment

`ops/windows/reboot/Set-WdTaskConsoleContainment.ps1` runs in the fleet start
preflight (`start-wd-all.ps1`). It checks that each known scheduled task,
`WD-ConsensusStallDetector` and `WD-AgentValue-Weekly`, is in one of two exact
forms:

- its original console form;
- its hidden form, run through `C:\Python\wd_silent_launch.exe`.

Any other action is "drifted" and stops the fleet start. With `-Apply`, the
script rewrites an original form into the hidden form.

## The bridge pin on WD-AgentValue-Weekly

`wd_agent_value_metric.py` requires `--bridge-bundle` and
`--bridge-manifest-sha256`. The weekly task therefore carries them after its
base arguments:

```
... --days 7 --post-bridge --bridge-bundle "C:\Python\wd-reboot-bundles\<40-hex commit>" --bridge-manifest-sha256 <64 uppercase hex>
```

The containment accepts this pin, and only this pin, when all of these hold:

- the task is WD-AgentValue-Weekly, the only job declared with `bridge_pin`;
- the pin follows either base form exactly, with nothing after it;
- it names an existing bundle directory under `C:\Python\wd-reboot-bundles`,
  and neither that directory nor its manifest is a reparse point;
- that bundle's `deployment-manifest.json` hashes, by SHA256, to the given value.

When `-Apply` wraps the task into its hidden form, it keeps the verified pin.

## Why

On 2026-09-27 a cold-boot rehearsal found a boot blocker. The rehearsal is
`start-wd-all.ps1 -DryRun` run from a source worktree; it models the next cold
start without the live lanes.

The pin had been added to the task when the metric started requiring it, but
the containment still expected the form without it. `start-wd-all.ps1 -Auto`
after a reboot would therefore have stopped with "scheduled console task action
drifted: WD-AgentValue-Weekly".

As a stopgap, the task was reset to the bare hidden form. The old definition is
kept in `.codex-audit/WD-AgentValue-Weekly.before-20260927.xml` in the fable-5
worktree. Once this change is deployed, the task can carry the pin again,
pointing at the current bundle, with an empty working directory.
