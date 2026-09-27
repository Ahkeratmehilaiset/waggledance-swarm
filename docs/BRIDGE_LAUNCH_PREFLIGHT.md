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
   - for Claude, the launcher's own `--settings` file;
   - for a Claude launch that resumes a recorded conversation, `--claude-resume-thread`.
     A resumed session "keeps the model it was using when the transcript was saved"
     (model-config.md), so the preflight reads that transcript, not the settings file.
2. The probe resolves the effective model and effort with `tools/lane_effective_model.py`
   (PR-7a: the CLIs' documented precedence, fail closed) and classifies the result against
   the signed catalog. The resolved values, their sources and the verdict go into the lane's
   `lane_profiles/launch-shadow.jsonl` entry under `preflight`.
3. **The status code is the only signal the launcher reads:**
   - `0`: the verdict is `allowed` (or no `--cli` was given);
   - `3`: attention. The verdict is `below_floor`, `not_in_lane_allowlist`,
     `provider_mismatch` or `unknown`, or the resolver itself failed;
   - any other code, or a probe that cannot run at all: the preflight is **unavailable**
     (claude-rco-2 N2 on #1745). Without this, a broken probe would look exactly like an
     allowed launch.
4. On attention or unavailable, when a preflight was requested, the launcher posts **one**
   bridge event with the pinned writer, under the lane's own identity:
   - type `status`, task `lane-profile-switching`, to `operator,codex-lead-1`;
   - status `launch_preflight_attention` or `launch_preflight_unavailable`, each with its
     own fixed message;
   - a payload of lane, launcher, run id, `preflight` (`attention` / `unavailable`),
     `enforcement: alert_only` and the path of the shadow log.

   The identity shape (agent, run id, role, agent uuid, session id = run id, capabilities)
   is the one both launchers already use for the events they post on every launch, such as
   `target_state_manifested`, so the pinned writer accepts it in production.

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

## Configuration directories and resumed sessions

The preflight reads the files the CLI will read:
- `CODEX_HOME` moves the Codex `config.toml`, and `CLAUDE_CONFIG_DIR` moves every Claude
  settings file and the transcripts. Both launchers honour them, and so does the preflight
  (claude-rco-1 B1 on #1745). A settings `env` block that sets `CLAUDE_CONFIG_DIR` fails
  closed.
- A resumed Claude launch keeps its transcript's model. `--model` and `ANTHROPIC_MODEL`
  still win, and so does `ANTHROPIC_DEFAULT_MODEL` when a new session would start on it.
  The `ANTHROPIC_DEFAULT_*_MODEL` family variables fail closed on a resume. The model is
  the last main-thread assistant turn in the transcript tail; side threads and synthetic
  turns are skipped. Any of these makes it `unknown`:
  - a `/model` command after that turn;
  - an unreadable line before it;
  - a turn without a model;
  - a missing or linked transcript.
- Codex resumes on the current configuration: the CLI warns "resuming session with
  different model", and recorded threads change model across restarts. So a Codex resume
  reads `config.toml` like a fresh launch.

## Measured on this machine, 2026-09-27

| Lane | Effective (source) | Verdict |
|---|---|---|
| codex-lead-1, codex-tools-1, morning | gpt-6-luna / xhigh (`~/.codex/config.toml`) | `not_in_lane_allowlist` |
| codex-lead-1, codex-tools-1, after the operator's change | gpt-6-sol / high (`~/.codex/config.toml`) | `allowed` |
| fable-5, fresh launch | built-in default model; user `effortLevel` not applicable | `unknown` |
| fable-5, resumed (its real launch) | claude-opus-5-5 (transcript) / xhigh (`modelSettings`) | `allowed` |

Until the operator pins profiles, a launch on an unpinned default posts an attention event.
That is the truth about the fleet, made visible at the moment it matters.

## The operator's planted-fault check

Put `model = "gpt-6-luna"` and `model_reasoning_effort = "low"` in a Codex config and launch
Lead: the probe exits `3` and one `launch_preflight_attention` event appears. The tests
reproduce this with a temporary config (`test_the_planted_luna_low_fault_exits_attention`),
and with PowerShell stubs for both launcher functions (exactly one fixed event, nothing
leaked, a writer failure never breaks the launch).
