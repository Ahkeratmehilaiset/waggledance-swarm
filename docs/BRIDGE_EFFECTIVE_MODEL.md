# Effective lane model (lane profile switching PR-7a)

Fleet lanes launch with `model: native`: the launcher passes no model or effort, so each
CLI falls back to its own configuration. That configuration is **shared by sibling
lanes**:

- `~/.codex/config.toml` for codex-lead-1 and codex-tools-1;
- `~/.claude/settings.json` for claude-rco-1, claude-rco-2 and fable-5.

A single `/model` or `/effort` in one window (both persist "as your default for new
sessions"), or one edit to `config.toml`, silently changes every sibling's next launch.
On 2026-09-27 the operator set Codex to `gpt-6-luna` for Tools, which also moved Lead.

`tools/lane_effective_model.py` resolves, from the same sources the CLI reads, which
model and effort a launch will actually get and where each value comes from. It then
classifies the result against the signed catalog. It is read-only and wired to nothing.
The launch preflight that turns a non-`allowed` result into a bridge event (and, with a
signed catalog, a refusal) is PR-7b.

## Precedence

**Claude Code**, documented in code.claude.com/docs/en/settings.md "Settings precedence",
model-config.md and env-vars.

Model:
1. managed settings;
2. `--model`;
3. `ANTHROPIC_MODEL` (it overrides every settings file's `model`);
4. the `--settings` file;
5. project `.claude/settings.local.json`;
6. project `.claude/settings.json`;
7. user `~/.claude/settings.json`;
8. `ANTHROPIC_DEFAULT_MODEL` (only when no file sets `model`);
9. the built-in default.

Effort:
1. managed settings;
2. `--effort`;
3. the `--settings` file;
4. project local;
5. project;
6. user.

Within one settings file, the per-model `modelSettings.<model>.effortLevel` and the global
`effortLevel` may both be present. When they disagree, their precedence is not
documented, so the effort is **ambiguous** and therefore unknown. `effortLevel: auto`
means the model's tuned default, which is unknown.

**Codex CLI:**
1. `--model` and `-c model_reasoning_effort=` on argv;
2. the `[profiles.<name>]` selected by `config.toml`'s `profile`;
3. the top-level `model` and `model_reasoning_effort`;
4. the built-in default.

A project `.codex/config.toml` inside the worktree that sets a model, effort or profile
is reported with `project_config_precedence_unverified`, and the result is unknown.

## Fail closed

The resolver never guesses. A value from a built-in default, an alias (`opus`,
`sonnet`, `default`, ...), an unreadable or ambiguous source, or a managed-settings file
is `None`, with the reason in `issues`. A source counts as unreadable when it is:
- not JSON or TOML;
- a JSON value that is not an object, or one with duplicate keys;
- over 1 MiB;
- a symlink or reparse point.

`resolved` is true only when both model and effort are known and there are no issues.

## Classification

`classify(catalog, lane, resolved)` returns one verdict:
- `allowed`: in the lane's `allowed_profiles` at or above its floor (the reason says
  `default` or `in_allowlist`);
- `below_floor`;
- `not_in_lane_allowlist`, with reason `in_catalog_for_other_lanes` or `not_in_catalog`;
- `provider_mismatch`;
- `unknown`: carries the resolver's issues.

The CLI exits `0` for `allowed`, `3` for anything that needs attention and `2` for an
error.

## Measured on this machine, 2026-09-27

| Lane | Would launch on | From | Verdict |
|---|---|---|---|
| codex-lead-1, codex-tools-1 | gpt-6-luna / xhigh | `~/.codex/config.toml` | `not_in_lane_allowlist` (not in the catalog) |
| claude-rco-1, claude-rco-2, fable-5 | built-in default model / xhigh | no `model` key; effort from `~/.claude/settings.json` | `unknown` (unpinned default model) |

The planted-fault check is a test: a `luna / low` config for Lead is never `allowed`.
