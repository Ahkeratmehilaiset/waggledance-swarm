# Launch preflight (lane profile switching PR-7b)

Every launch now checks **which model and effort the lane will actually start on**. It
turns a problem into a bridge event instead of a sentence in a report.

The rule behind it: in this class of problem a finding is a fail-closed gate, never prose.
It came out of the 2026-09-27 luna/low episode. The danger was already written in a report,
but nothing escalated it, because it was a sentence and not a gate.

## How it works

1. `start-wd-agent.ps1` and `start-wd-tools-consumer.ps1` already run the PR-4 launch probe
   (`tools/lane_profile_launch_probe.py`) through the pinned bridge package just before argv
   is built. They now also pass:
   - `--cli` (`claude` / `codex`);
   - the lane `--worktree`;
   - for Claude, the launcher's own `--settings` file.
2. The probe resolves the effective model and effort with `tools/lane_effective_model.py`
   (PR-7a: the CLIs' documented precedence, fail closed) and classifies the result against
   the signed catalog. The resolved values, their sources and the verdict go into the lane's
   `lane_profiles/launch-shadow.jsonl` entry under `preflight`.
3. **The status code is the only signal the launcher reads:**
   - `0`: the verdict is `allowed` (or no `--cli` was given);
   - `3`: attention. The verdict is `below_floor`, `not_in_lane_allowlist`,
     `provider_mismatch` or `unknown`, or the preflight could not run.
4. On `3`, the launcher posts **one** bridge event with the pinned writer, under the lane's
   own identity:
   - type `status`, status `launch_preflight_attention`, task `lane-profile-switching`,
     to `operator,codex-lead-1`;
   - a fixed message;
   - a payload of lane, launcher, run id, `enforcement: alert_only` and the path of the
     shadow log.

   The launcher never parses the probe's output. The details live in the shadow log, so no
   probe text can reach argv, the model, the effort or the event fields.
5. **The launch always continues (alert mode).** A failing writer, a missing writer or a
   failing probe never breaks a launch, and nothing reaches the launcher's success stream.

## Why alert-only for now

Today every lane launches `native`:
- the Claude lanes resolve to an **unpinned built-in default model**, so they are `unknown`;
- the Codex lanes resolve to a shared `config.toml` value outside their allow-lists.

Refusing to launch now would stop the whole fleet. Enforcement belongs to PR-9, where the
launcher passes each lane's signed profile explicitly (`--model` / `--effort`; for Claude a
per-lane `--settings` layer). The effective profile is then resolved from argv and can be
`allowed`, and the gate can refuse without an outage. PR-9 is operator-signed.

## Measured on this machine, 2026-09-27

| Lane | Effective (source) | Verdict | Launch |
|---|---|---|---|
| codex-lead-1, codex-tools-1 | gpt-6-luna / xhigh (`~/.codex/config.toml`) | `not_in_lane_allowlist` | continues, attention event |
| claude-rco-1, claude-rco-2, fable-5 | built-in default model / xhigh (`~/.claude/settings.json`) | `unknown` | continues, attention event |

Until the operator pins profiles, **every launch will post an attention event**. That is
the truth about the fleet today, made visible at the moment it matters.

## The operator's planted-fault check

Put `model = "gpt-6-luna"` and `model_reasoning_effort = "low"` in a Codex config and launch
Lead: the probe exits `3` and one `launch_preflight_attention` event appears. The tests
reproduce this with a temporary config (`test_the_planted_luna_low_fault_exits_attention`),
and with PowerShell stubs for both launcher functions (exactly one fixed event, nothing
leaked, a writer failure never breaks the launch).
