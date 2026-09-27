# Bridge profile cost meter (lane profile switching PR-13)

`tools/wd_profile_cost_meter.py` measures what each lane profile actually costs.
It reads local session logs read-only and prints one JSON report. It never
launches, switches, writes or signals anything, and the report always carries
`execution_allowed: false`. It is the measurement that the pacer (PR-5), the
demand policy (PR-11) and any context-window change can be checked against,
before and after.

Only token counts, model and effort ids, lane names and timestamps go into the
report. Prompt and response content is never read into it.

## Run

```
python tools/wd_profile_cost_meter.py [--hours 48] \
  [--store C:\Python\wd-capacity-observer\observations.sqlite] \
  [--fleet-manifest ops/windows/reboot/wd-fleet.json]
```

- `--claude-projects` defaults to `$CLAUDE_CONFIG_DIR/projects`, else `~/.claude/projects`.
- `--codex-sessions` defaults to `$CODEX_HOME/sessions`, else `~/.codex/sessions`.
- `--store` adds the capacity observer's pool samples, read with the pacer's
  `read_samples`.
- `--fleet-manifest` maps worktrees to lanes. It defaults to the tracked
  manifest. `''` turns the mapping off, so only directory names are used.
- `--hours` must be in (0, 744].

## Sources

**Claude Code transcripts**

The meter reads `projects/*/<session>.jsonl` and the subagent transcripts
`projects/*/<session>/subagents/*.jsonl`.

- One API response is written as several lines that share `message.id`. Turns
  are therefore deduplicated by (session, message id).
- Subagent turns are real API calls and count. `<synthetic>` turns do not.
- A session transcript names its lane in an `agent-name` or `custom-title`
  record. A subagent transcript has neither, so it takes the lane of its session
  transcript.

**Codex rollouts**

The meter reads `sessions/**/rollout-*.jsonl`.

- A turn's usage is the growth of the session's cumulative `total_token_usage`.
  A repeated event adds nothing, and a total that falls starts a new base.
- The lane is looked up in this order:
  1. the fleet manifest lane whose `worktree` is the session's `cwd`
     (the Lead works in `C:\Python\project2`);
  2. otherwise, the lane id at the start of the `cwd` directory name.

**Pool samples**

Codex `rate_limits` snapshots name a limit bucket in `limit_id`.

- The main `codex` bucket, or a snapshot with no id, gives the `primary` and
  `secondary` pools.
- Any other bucket is kept as its own `<limit_id>:<window>` pool and never mixed
  into the main one. `premium` has been seen, always with empty windows; it is
  not documented by the vendor.
- Observer samples follow the same rule: a `limit_id` other than the provider's
  own name gets its own pool.

`sources.<provider>.last_turn_by_lane` gives the newest recorded turn per lane. A
lane whose transcript stopped being written shows an old timestamp here, instead
of silently looking idle. On 2026-09-27 both RCO transcripts had not been
written since their 2026-09-26 relaunch, so their Claude use after that is
missing from any report.

## Report

- `profiles` — per `provider:model:effort`:
  - turns and subagent turns;
  - token classes;
  - weighted tokens, and weighted tokens per active hour (active 5-minute bins);
  - each lane's share.
- `lanes` — the lane anatomy, per `provider:lane`:
  - the request count;
  - context tokens per request (`p50`, `p90` and `max`, nearest rank);
  - `cache_read_share`, the share of the lane's weighted tokens that were cache
    reads;
  - `small_output_share`, the share of requests that wrote fewer than 300 output
    tokens.
- `pools` — pool attribution, per `provider/window`:
  - percentage points per million weighted tokens, pool-wide and for each
    profile that had at least 80% of a segment's tokens;
  - unexplained growth (no local tokens in the segment);
  - falling segments, which are skipped.

## Weighted tokens are a proxy

Subscription pools are not billed per token. Each token class is weighted by its
API price ratio (`WEIGHTS`):

| Provider | input | cache write | cache read | output |
|---|---|---|---|---|
| Claude | 1 | 1.25 | 0.1 | 5 |
| Codex | 1 | 1 | 0.1 | 8 |

How much a pool's percentage points actually move per weighted token is exactly
what the pool attribution measures. Whether a pool counts cache reads at 0.1 is
not documented by either vendor. Pool percentages are integers, so an estimate
built on fewer than 3 points is flagged `low_precision`.

## What the first measurement shows (2026-09-27, 24 h)

| Lane | Requests | Context p50 / p90 | Cache-read share | Small-output share |
|---|---|---|---|---|
| claude:fable-5 | 1288 | 523k / 878k | 86% | 26% |
| codex:codex-lead-1 | 1903 | 159k / 218k | 75% | 69% |
| codex:codex-tools-1 | 1075 | 147k / 211k | 68% | 52% |

The Codex primary pool moved 0.17 points per million weighted tokens, over
16 points.

Every tool call is one API request that reads the whole context again. A lane's
cost is therefore roughly its request count times its context size, and the
length of its answers matters much less. This snapshot points to three things:

- **Context size.** Claude lanes with a 1M window compact only near the limit,
  at about 967K by default. Their context per request is three times the Codex
  lanes'. Claude Code sets the window with `CLAUDE_CODE_AUTO_COMPACT_WINDOW`,
  `--autocompact` or `autoCompactWindow` (100K to 1M). Codex has
  `model_auto_compact_token_limit`. `/autocompact` saves to the user settings
  that every lane on the machine shares, so it is not a per-lane control.
- **Request count.** Most Codex requests, and a quarter of Claude requests, write
  under 300 output tokens. Each of them still reads the whole context. Batching
  several commands into one script call, and running independent calls in
  parallel, removes such requests.
- **Idle turns.** A heartbeat turn with nothing to do still reads the whole
  context. With a Monitor armed as the primary wake, a longer heartbeat
  (the ScheduleWakeup maximum is 3600 s) costs less.

This tool changes none of these settings. Any change is its own reviewed change,
checked against this report before and after.

## Limitations

- The weights are API price ratio proxies. The pool attribution measures them.
- Integer pool percentages need several points for precision.
- Grok is measured separately, by the Grok calibration log.
- Claude effort is the session effort recorded on each turn.
- A transcript that is not being written (see `last_turn_by_lane`) cannot be
  measured.
