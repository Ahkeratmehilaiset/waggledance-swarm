# Bridge Mind sandbox (code only)

Operator directive 2026-10-13, relayed by RCO1: the shadow mode becomes a real sandbox. Every lane connects to the
Mind and makes it faster and more correct in light rounds. Only a measured improvement goes to `main`, once,
through the normal PR path.

This branch family holds code only. Data (bridge captures, snapshots, run outputs) lives on the operator-chosen
sandbox data disk and never goes to git. The repository is public.

## Layout

* `sandbox/mind-main` (fable-5 merges lane fixes here without a review round). It is stacked on the Mind
  heads: slice 1 `6d60d69e` (workspace context and shadow pipeline), core and pager `5292f625` (Tools), board
  `d5ae03f2` and the X2 board fixture patch.
* `sandbox/mind-<lane>-*`: one branch family per lane, stacked on `sandbox/mind-main`.
* Data root: `<sandbox root>/<lane>/`. Each lane writes only its own directory. The live outputs of fable-5's
  producer are under `<sandbox root>/fable-5/.codex-audit/mind-live/`, and `latest.json` is the one standard
  path, written last and atomically.

## The loop

1. Read `latest.json`, `latest-board.txt` and your own lane and PR lines.
2. Report wrong, missing or stale facts as `type=message` on `fable-5/bridge-mind-shadow-live-20261013`, with the
   ref and the capture sha. No request id is needed.
3. Fix on your sandbox branch. Copy that cycle's `capture.json` and `context.json` into your own data dir, then
   rerun with your code:
   `python -B -m tools.wd_bridge_mind_shadow --capture C --context X --audit-root <yours>/.codex-audit --out-dir <new>`.
   Post one line: what changed, the metric before and after, the capture sha.
4. Grok joins once per round, one consultation at a time, in rotation. The prompt is under 12 KB and is built
   from the round summary and metrics only; it is scanned before launch, and the prompt and report hashes are
   posted.
5. The final measured version goes once through PR, CI, both RCOs, build votes and the class check.

Metrics: time to answer what a PR needs and what my next action is, bytes and events read per decision, wrong
facts per hour, rebuild and query speed.

## Hard rules (always)

* No import or call of gate code: verify_bridge_consensus, check_bridge_changes_requested,
  check_rco_pass_present, idle_consensus_auto_merge, the rule-12 evaluator, the merge and receipt executors.
* No bridge event writes from sandbox code. Agents report themselves.
* Writes only under the sandbox data root. No network, provider call, model install or download in experiment
  code.
* No sandbox data in git. A decoded privacy guard runs on every output.
* The Mind is advisory. Before any vote or pass, check with the canonical tools.

## The capture producer

`sandbox/mind/mind_capture_producer.py` is the one component with input I/O and the subject of RCO1's boundary review.

* **Bridge reads.** It reads the bridge only through the inherited pinned `Read-AgentBridge.ps1 -Compact` path:
  refs after a position cursor, then `-Compact -EventId` for each full row, and each canonical ref must match. A
  non-Compact read (`-Raw` included) drains the accepted queue under the global publication fence, so the
  producer never uses one.
* **GitHub reads.** Its read-only GitHub metadata calls (`gh pr list` and `gh pr view` for files and commits)
  are the only network touch. Whether they are allowed is a carve-out that Lead decides.
* **Experiment code.** Experiment code stays offline and reads only the copied capture files.
