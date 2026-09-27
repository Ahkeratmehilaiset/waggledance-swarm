# Lane context window (lane profile switching PR-17)

A lane entry in `ops/windows/reboot/wd-fleet.json` may set `auto_compact_tokens`,
a whole number from 100000 to 1000000. When the entry is present,
`start-wd-agent.ps1` passes it to the lane's CLI at launch:

| CLI | Argument |
|---|---|
| Claude Code | `--autocompact <n>` |
| Codex | `-c model_auto_compact_token_limit=<n>` |

A lane without the key keeps its CLI's own default. A key that is present but
malformed stops the launch before any launch work: a string, a fraction, a
boolean, null, a list or an object, or a number outside the range.

## Why

Every tool call is one API request that reads the whole context again, so a
lane's cost is roughly its request count times its context size.

- Claude Code's 1M-window models compact only near the limit, at about 967K by
  default.
- On 2026-09-27 the fable-5 lane read a median 523k tokens per request, and 86%
  of its weighted tokens were cache reads (docs/BRIDGE_PROFILE_COST.md).
- The Codex lanes, which compact on their own, read 150–160k.

## Why at launch, not with `/autocompact`

`--autocompact` sets the window for that one launch, and a higher settings scope
does not override it. `/autocompact` would instead save the value to the user
settings, which every lane on the machine shares.

`CLAUDE_CODE_AUTO_COMPACT_WINDOW` overrides the flag while it is set. The
launcher does not set it.

## The first setting

Only fable-5 sets a window, 400000, as a one-lane experiment the operator
approved on 2026-09-27. The experiment is checked with
`tools/wd_profile_cost_meter.py` before and after:

- the lane anatomy (context per request, cache-read share);
- the pool points per million weighted tokens.

Other lanes stay at their CLI default until that measurement is in.

## When it takes effect

The fleet manifest is part of the deployed reboot bundle. A changed window takes
effect when a bundle generation that contains it is deployed and the lane is
next launched. It does not change a running session.
