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
settings-reference.md (`effortLevel`, `modelSettings`, `maxEffortLevel`, `ultracode`, `env`),
model-config.md "Adjust effort level" and env-vars.

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
1. `CLAUDE_CODE_EFFORT_LEVEL` ("takes precedence over both" `--effort` and `effortLevel`);
2. `--effort` (`--effort ultracode` runs at `xhigh`);
3. the `ultracode` setting: `true` runs at `xhigh` and "takes precedence over `effortLevel`
   and `modelSettings` entries". The highest-precedence file that sets the key decides;
4. settings files, per model: the highest-precedence file (`--settings`, project local,
   project, user) that sets either that model's `modelSettings.<model>.effortLevel` or a
   top-level `effortLevel` that applies to the model decides. Within one file, the
   model's own level wins over the top-level key;
5. the model's default, which is unknown here (an organization can change it).

A top-level `effortLevel` in the **user** file is the older form `/effort` wrote. It still
applies to Opus 5, Fable 5.1 and earlier models, but "Opus 5.5 and models released after
it ignore it". The module lists the models it applies to (`USER_EFFORT_LEVEL_APPLIES`,
with Sonnet 5 placed as earlier by the CLI version that added it: v2.1.197, before Opus 5
at v2.1.219 and Opus 5.5 at v2.1.280). For a model in neither list, including every future
model, a user-file `effortLevel` that would decide gives
`user_effort_level_applicability_unverified`. So the list fails closed, not open (B4 from
claude-rco-2, with claude-rco-1 concurring).

`modelSettings` entries match by the model's canonical id: `claude-opus-5-5[1m]` and a
dated id use the `claude-opus-5-5` and undated entries. An alias, suffixed or dated
**key** for the model is not documented as read, so it fails closed.

These also fail closed:
- any `maxEffortLevel` cap below `max` that may apply to the model, top-level or per
  model, in any file (a cap lowers every source, `--effort` and the variable included);
- `--effort` other than `xhigh` together with `ultracode: true` (their order is not
  documented);
- `CLAUDE_CODE_EFFORT_LEVEL=ultracode` (documented as not accepted);
- a settings `env` block that sets `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_MODEL` or
  `CLAUDE_CODE_EFFORT_LEVEL` (its order against the process environment is not
  documented);
- a non-string `model` or `effortLevel`, a non-boolean `ultracode`, and a malformed
  `modelSettings`.

`auto`, from a file or the variable, means the model's tuned default, which is unknown.

**Codex CLI:**
1. `--model` and `-c model_reasoning_effort=` on argv;
2. the top-level `model` and `model_reasoning_effort` in `$CODEX_HOME/config.toml`
   (default `~/.codex`);
3. the built-in default.

Since Codex 0.134.0 a profile is a separate `<name>.config.toml`, chosen only with
`--profile`. The OpenAI docs (config-advanced) say the old `profile = "..."` selector and
inline `[profiles.<name>]` tables are "no longer supported". The launchers never pass
`--profile`, and what Codex 0.157 does with the legacy keys is not verified. So either key
gives `codex_legacy_profile_unsupported:<key>` and the result is unknown; it is never read
as the active profile (codex-tools-1 B2).

A project `.codex/config.toml` inside the worktree that sets a model, effort or profile
is reported with `project_config_precedence_unverified`, and the result is unknown.

## Configuration directories

`CLAUDE_CONFIG_DIR` ("All settings ... are stored under this path") and `CODEX_HOME` move
the user settings, the server-managed cache and the Codex `config.toml`. The launchers
honour both, and so do the resolver CLI's defaults (codex-tools-1 B1). A settings `env`
block that sets `CLAUDE_CONFIG_DIR` fails closed, and a blank value means the default
directory.

## Managed settings

Claude Code managed settings come in three forms, and all three are checked:
- the `C:\Program Files\ClaudeCode\managed-settings.json` file (the legacy
  `C:\ProgramData` path is not read by the CLI);
- a `Settings` registry value (REG_SZ or REG_EXPAND_SZ) under
  `SOFTWARE\Policies\ClaudeCode`, in HKLM (Group Policy or MDM) or in HKCU (user-scoped);
- server-managed settings from the claude.ai console, through their local cache
  `~/.claude/remote-settings.json` (claude-rco-1 B2).

Any of these makes the result `managed_settings_present`, and so unknown:
- a present file or cache, even an empty one, whose own values are never read;
- an unreadable file or cache;
- a present, non-blank registry value;
- a registry key that exists but cannot be read;
- a registry that raises.

**Not modelled offline.** These live in the account, not on this machine, so a
`resolved` result cannot rule them out:
- server-managed settings on a first launch, before any cache exists;
- an organization default model that overrides the user's selection;
- organization effort limits.

Managed settings can pin or cap the model and effort, which this module does not model
(claude-rco-2 review of #1744). The tests exercise the real registry reader against a
throwaway HKCU key; the real policy key is never written.

## Fail closed

The resolver never guesses. A value from a built-in default, an alias (`opus`,
`sonnet`, `default`, ...), an unreadable or undecidable source, or any form of managed
settings is `None`, with the reason in `issues`. A source counts as unreadable when it is:
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
| claude-rco-1, claude-rco-2, fable-5 | built-in default model / unknown effort | no `model` key; the user file's `effortLevel` cannot be applied to an unknown model | `unknown` (unpinned default model) |

Later the same day, after the operator moved `config.toml` to `gpt-6-sol / high`, both
Codex lanes resolve to `gpt-6-sol / high`, `allowed`.

The planted-fault check is a test: a `luna / low` config for Lead is never `allowed`.
