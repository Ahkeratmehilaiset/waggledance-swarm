# Bridge model registry (lane profile switching PR-6)

`configs/model_registry.json` records the models the swarm's CLIs can run, per model
and reasoning effort. `tools/wd_model_registry.py` turns it into a read-only value
analysis. Nothing here selects, writes or signals anything, and every report carries
`execution_allowed: false`. A public benchmark is not our workload: it informs a
proposal. Our qualification and measured quota burn decide, and every profile change
still goes through the signed catalog (BRIDGE_LANE_PROFILES.md) and the pacer
(BRIDGE_CAPACITY_PACING.md).

## The registry file

- **One comparable source.** Every row uses the same benchmark: the Artificial Analysis
  Intelligence Index (`quality`) and its cost per index task at API prices (`cost`).
  The version, fetch date and source URLs are part of the file. Mixing sources would
  make rows incomparable.
- **Rows.** A model is keyed `"<provider>/<model>"` and lists its efforts
  (`low` / `medium` / `high` / `xhigh` / `max`), each with `intelligence_index` and
  `usd_per_task`. `coding_agent_index` holds the published Coding Agent Index at max
  effort, or `null`.
- **Benchmark variant.** An optional `benchmark_variant` labels the variant the scores come
  from. Artificial Analysis labels the Claude Opus 5.5 and Fable 5.1 effort rows "with
  fallback", so those two models carry `benchmark_variant: "with_fallback"`. The label
  travels with every row, frontier entry and suggestion in the report, so a fallback score
  is never read as the measured behaviour of one fixed CLI model (codex-tools-1 N2 on #1743).
  Dominance and value are benchmark-only, and the report's `limitations` says so.
- **Strict validation.** The file is refused if it has:
  - an extra or missing key at any level, a duplicate key, or a non-finite number
    (`benchmark_variant` is the only optional key, and it must be a non-empty string);
  - a boolean used as a number, a negative value or one out of range;
  - an unknown provider (`codex`, `claude` and `grok` are known) or an unknown effort;
  - a key that does not match its provider and model;
  - a non-https source, or a file over 256 KiB, a symlink or a reparse point.
- **Path checks.** Only the registry file itself is checked for a symlink or reparse point, not its
  ancestors. The registry lives at a fixed, git-tracked path (`configs/`), not in a per-lane runtime
  directory, and any redirected file would still have to pass the same strict validation
  (claude-rco-2 review of #1743). On POSIX the open also uses `O_NOFOLLOW`, so a symlink swapped in
  after the check fails the open. Windows has no such flag: a reparse point swapped in between the
  check and the open is **not** closed. This is a disclosed TOCTOU gap, and the fixed path plus the
  strict validation are the only mitigation.
- **Updating.** New models and fresh numbers arrive as a PR, normally from the
  post-boot orientation analysis (plan v2, PR-14). They are data, not authority.

## Schema v2: dated evidence (`wd.model-registry.v2`)

The shipped file is v2. v1 files still load and analyse identically (deliberate read
compatibility), and a v1 document may not carry the v2 tables.

- **Historical block.** `historical` marks exactly the v1 tables (`benchmark`,
  `coding_benchmark`, `models`) as `historical` evidence, with `source_measured_at`, its
  basis, and `cost_basis: "api_price_not_quota"`. API dollars are never quota units.
- **Pools** (`pools.<id>`, exact keys `provider`, `limit_id`, `window`, `tier`, `verification`,
  `provenance`, `measured_at`, `ttl_seconds`). A `verified` pool needs:
  - a measuring provenance (`operator_reading`, `local_measurement` or `f21_receipt`);
  - a `limit_id`;
  - a `measured_at` date;
  - a `ttl_seconds` from 1 s to 400 days.

  A verified pool expires. `pool_state(pool, now)` returns:
  - `verified` while `now - measured_at <= ttl_seconds`, and `stale` after that;
  - `unknown` when the pool is dated more than 5 minutes ahead, or the clock is unusable (see below);
  - `unverified` for an unverified pool, whatever the clock.
- **Candidates** (`candidates."<provider>/<model>"`) are unrated models. They always have
  `admission: "none"` and `capability: "unknown"`, and may name a pool of the same provider.
- **Observations.** Each has a unique `id` and these fields:
  - a `subject` (`provider`, `model`, `effort`, `pool`);
  - `kind`, `class`, `unit`, `value` and `status`;
  - `provenance`, `measured_at` and `ttl_seconds`;
  - an `uncertainty` (`kind`, `low`, `high`, `note`).

  The rules:
  - `measured` needs a measuring provenance, a date, a TTL and a stated uncertainty. That is
    either an `interval` that contains the value, or `exact` with a non-empty justification `note`.
    `none_stated` and `unknown` never make a value measured.
  - An `interval` is refused for the categorical kinds `tier` and `pool`, because containment is
    meaningless for a label.
  - An API-dollar unit never names a pool, and a quota unit or a limit needs `subject.pool`.
  - An observation never verifies an unverified pool.
  - `observation_state(observation, now)` is `fresh` inside the TTL and `stale` after it. It is
    `unknown` if the observation is dated more than 5 minutes ahead or the clock is unusable. The
    `historical`, `unverified` and `unknown` statuses never depend on the clock.
- **The evaluation clock.** Both public state functions normalize `now` to aware UTC. The result is
  the conservative `unknown`, never an exception and never local time, when `now` is:
  - `None` or not a datetime;
  - naive, including a tzinfo that names no offset;
  - not representable in UTC.

  `model_table` still refuses a plain naive time (`tzinfo` is None) with a `RegistryError`. Any
  other unusable time makes every freshness-dependent cell `unknown`, and `generated_for_utc` is null.

## The model table (advisory)

`model_table(registry, now)` gives one row per model and effort (rated models and candidates).
Every cell is explicit: `{value, state, source, measured_at, note}`. A stale or unknown cell has no
value, and the table carries `execution_allowed: false`.

- **One cell from all its candidates.** Resolution goes in this order:
  - The best state wins: fresh, then historical, then unverified, then stale, then unknown.
  - Within that state, the latest dated `measured_at` wins. Undated candidates are excluded
    whenever any candidate is dated.
  - Different values at that latest time give `conflict` with no value; nothing wins silently.
  - Older values that disagree are counted in `superseded`.
- **Pool verification only through a fresh membership.** `pool_verification` is one of:
  - `unknown` for v1, or when the membership cell is unknown (no membership, or an unusable clock);
  - `membership_<state>` when the model's pool-membership cell is not fresh (for example
    `membership_stale` or `membership_conflict`);
  - otherwise, the `pool_state` of the named pool.
- **Limitations travel with the table:**
  - unknown is not zero, and stale values are unknown;
  - historical benchmarks are not our workload;
  - an API price is never quota cost;
  - candidates have no admission;
  - pool verification needs a fresh membership;
  - an equal-state disagreement is a conflict, not a choice.

## The analysis

```
python tools/wd_model_registry.py --current-profiles "{\"claude-rco-1\": \"claude-sonnet-5:xhigh\"}"
```

- **Frontier.** Per provider, the rows no other row of the same provider dominates.
  Row A dominates B when A's quality is at least B's, its cost is at most B's, and one
  of the two is strictly better. A dominated row is never worth selecting. Rows of
  different providers never dominate each other, because a lane is bound to one
  provider.
- **Lane value table.** For each catalog lane and its current `model:effort`:
  - `more_for_same_money`: the best quality at no higher cost;
  - `same_for_less`: the lowest cost at no lower quality.

  Neither suggestion ever lowers quality, so reviewer lanes (raise-or-same) are
  respected by construction. Each suggestion carries its quality delta, its cost delta
  in percent, its `coding_agent_index`, its catalog profile (if any), `approved`, and
  `cli_available`. A general quality index can hide a weaker coder, as with GPT-6
  Luna against GPT-6 Sol, so a coding lane needs the coding index too.

  An unknown current profile gives `current_unknown`, and one with no registry row
  gives `current_unrated`. Neither is ever guessed.
- **Catalog diff.** It lists:
  - `propose_add`: frontier rows the catalog lacks;
  - `dominated`: catalog profiles that another row beats, with the best replacement;
  - `unrated`: catalog profiles with no registry row.
- **CLI availability.** Codex rows are checked against the Codex CLI's own
  `$CODEX_HOME/models_cache.json` (model slug and supported efforts), resolved when the
  report runs. The default is `~/.codex`, and a blank `CODEX_HOME` means the default. An
  explicit `--codex-models-cache` path wins. Earlier the default was fixed to `~/.codex` at
  import, so a moved Codex home could mark a model unavailable from the wrong cache
  (codex-tools-1 N1 on #1743). Claude publishes no
  local model list, so Claude rows are `cli_available: null`, unverified until a real
  turn observes the model.

## Snapshot, 2026-09-27

The catalog's `claude-sonnet-5-xhigh` (index 34, $2.87 per task) is dominated by
Opus 5.5 high (54, $1.82). `codex-gpt-5.6-sol-medium` is dominated by GPT-6 Sol high,
and `codex-gpt-5.6-terra-medium` by GPT-6 Luna max (its coding index is weaker; see
above). The Claude frontier is Sonnet 5 low, then Opus 5.5 low to max. The Codex
frontier is GPT-6 Luna, then Sol, then Astra.
