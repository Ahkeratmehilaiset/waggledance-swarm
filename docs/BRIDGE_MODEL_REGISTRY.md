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
  (claude-rco-2 review of #1743).
- **Updating.** New models and fresh numbers arrive as a PR, normally from the
  post-boot orientation analysis (plan v2, PR-14). They are data, not authority.

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
