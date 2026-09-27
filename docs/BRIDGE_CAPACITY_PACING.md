# Bridge capacity pacing (lane profile switching PR-5)

`tools/wd_capacity_pacing.py` is a read-only pacer. Every agent can run it and
see the same answer: at the measured burn rate, where will each subscription
quota window stand when it resets? It then recommends, in shadow, which lanes
should move one step within the signed catalog. It never launches, stops, writes
or signals anything, and every report carries `execution_allowed: false`. The
planner (D5) and the executor (D4) remain the only path to a relaunch, and only in
a signed, non-shadow mode (see BRIDGE_LANE_PROFILES.md).

## Run

```
python tools/wd_capacity_pacing.py --store C:\Python\wd-capacity-observer\observations.sqlite \
  --current-profiles "{\"codex-lead-1\": \"codex-gpt-5.6-sol-medium\"}" [--work-mode production|planning|conserve]
```

`--current-profiles` maps lanes to their **verified** current profile, from the D3
session binding. A lane that is missing, or that names a profile outside its
allowed list, parks with `current_profile_unverified`.

## Pace per window

The pacer reads the capacity observer's store read-only. It opens a `mode=ro`
connection with one snapshot and refuses a WAL store, like
`bridge_capacity_collector.status`. It parses each row with the collector's own
`quota_details`, so windows are read exactly as the capacity status reads them.
Failed polls and malformed rows are skipped.

For each `(provider, limit_id, window)` it paces only the **current window
instance**: the samples that share the newest sample's reset time.

- **Rate** = rise in used percent between the first and last sample, per hour. It
  needs a span of at least 30 minutes and a counter that does not fall; otherwise
  the window is `rate_unknown`.
- **Forecast** = used + rate x hours to reset.
- **Verdict:**
  - `underused`: forecast at or below 70 %, so capacity expires unused;
  - `on_pace`: forecast between 70 % and 95 %;
  - `overrun`: forecast at or above 95 %, so the pool runs out before reset;
  - `exhausted`: already at 100 %;
  - `unknown`: with a reason (`measurement_stale` when the newest sample is older
    than 15 minutes, `window_already_reset`, `window_duration_unknown`,
    `rate_unknown`).

Claude windows carry no duration, so the known names are used: `five_hour` = 300 min,
`seven_day` = 10080 min. Codex supplies its own duration.

## Recommendation per lane

Lanes are taken in work-mode priority. In `production` and `planning` the order is
reviewer, then lead, then producer; `conserve` reverses it.

- **Park** when:
  - the current profile is unverified;
  - any window of the lane's pool is missing or unknown (`capacity_unknown`,
    listed in `blocked_by`).
- **On an overrun or exhausted window, lower**, or in `conserve` for a producer. The
  lane moves one step weaker unless it is a reviewer (`reviewer_never_lowered`) or
  already at its floor (`at_floor`).
- **Raise** when every long window (one day or longer) is `underused` and the lane is
  not at its strongest. Short windows only block (by overrunning) or lower; they
  never justify a raise.
  - Only one lane per pool is recommended to raise per evaluation
    (`raise_queued_one_step_per_pool`), so the next measurement shows its effect.
  - A target that is not approved in the catalog is shown with
    `target_not_approved` and parks; it does not take the pool's slot.
- **Same** otherwise: `on_pace` or `at_strongest`.

## Limits (stated in every report)

- `per_lane_cost_not_attributed`: pools are shared (three Claude lanes on one
  subscription; Codex quota is account-wide), so per-lane cost is not measured. The
  pacer steps one lane per pool and re-measures instead of dividing a budget.
  Attribution from per-session token usage is future work.
- `one_step_per_pool_per_evaluation`.
- Grok is listed under `unpaced_providers` as `capacity_unobserved`: the observer does
  not measure it, and nothing is inferred.

## Measured example, 2026-09-27

- Claude seven-day: 31 %, 0.33 %/h, 84 h to reset. Forecast 59 %, so `underused`.
- Codex weekly: 5 %, 0.34 %/h, 155 h to reset. Forecast 57 %, so `underused`.
- Every lane gets a `raise` target: Opus 5.5 xhigh on Claude and GPT-6-Sol high on
  Codex. All of them park with `target_not_approved` until the catalog is signed.
