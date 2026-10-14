# SPDX-License-Identifier: BUSL-1.1
"""Brainstorm item B, slice 1 (RCO2): compare V3 lane inboxes with lane-recorded gate truth.

Frozen definitions (posted on claude-rco-1/mind-brainstorm-5-rounds-20261014 before any result):

* gate_ready(PR, cycle)   = verify_bridge_consensus.ok AND check_rco_pass_present.ok AND
                            check_bridge_clear_to_merge.ok AND github.all_required_success is True,
                            read from RCO1's gate-truth file (main 227aa862, task = PR branch, head = GitHub head).
* v3_ready(PR, cycle)     = the PR is a "done" row with missing == [] and CI green (bridge green_reported or
                            github.ci_observed_green) in ANY of the five lane inboxes of that cycle (render_text READY).
* v3_filled(PR, cycle)    = missing == [] in any row (act/wait/own/done) of any lane inbox.
* false_ready             = v3_ready AND NOT gate_ready.                       target 0
* divergence              = v3_filled != verify_bridge_consensus.ok, over PRs present in both.
* unreported_divergence   = divergence where no lane row of that PR carries a binding tension
                            ("unbound_votes" non-empty).                          target 0
* controls: PR1902 (votes on PR1896's branch task) must be gate_ready False and, after the change,
  a reported divergence or not filled; PR1904 at b8e2d25b must stay v3_ready and gate_ready.

No gate code is imported or called and no bridge log is read: inputs are JSON files only.
Usage: python -B b_slice_compare.py TRUTH_DIR CYCLE INBOX_DIR [--label before|after]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

LANES = ("claude-rco-1", "claude-rco-2", "codex-lead-1", "codex-tools-1", "fable-5")
SECTIONS = ("act", "wait", "own", "done")


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def truth_rows(truth_dir: Path, cycle: str) -> dict[int, dict]:
    rows = {}
    for path in sorted(truth_dir.glob(f"gate-truth-{cycle}-PR*.json")):
        record = _load(path)
        rows[int(record["pr"])] = {
            "gate_ready": bool(record["verify_bridge_consensus"].get("ok")) and bool(record["check_rco_pass_present"].get("ok"))
            and bool(record["check_bridge_clear_to_merge"].get("ok")) and record["github"].get("all_required_success") is True,
            "consensus_ok": bool(record["verify_bridge_consensus"].get("ok")),
            "file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return rows


def inbox_rows(inbox_dir: Path) -> dict[int, dict]:
    seen: dict[int, dict] = {}
    for lane in LANES:
        path = inbox_dir / f"inbox-{lane}.json"
        inbox = _load(path)
        for section in SECTIONS:
            for item in inbox.get(section) or []:
                number = int(item["pr"])
                entry = seen.setdefault(number, {"ready": False, "filled": False, "unbound": False, "lanes": []})
                gh = item.get("github") or {}
                green = item.get("ci") == "green_reported" or gh.get("ci_observed_green") is True
                filled = item.get("missing") == []
                entry["filled"] |= filled
                entry["ready"] |= section == "done" and filled and green
                entry["unbound"] |= bool(item.get("unbound_votes"))
                entry["lanes"].append(f"{lane}:{section}")
    return seen


def compare(truth_dir: Path, cycle: str, inbox_dir: Path) -> dict:
    truth, v3 = truth_rows(truth_dir, cycle), inbox_rows(inbox_dir)
    both = sorted(set(truth) & set(v3))
    false_ready = [n for n in both if v3[n]["ready"] and not truth[n]["gate_ready"]]
    divergence = [n for n in both if v3[n]["filled"] != truth[n]["consensus_ok"]]
    unreported = [n for n in divergence if not v3[n]["unbound"]]
    controls = {str(n): {"v3_ready": v3.get(n, {}).get("ready"), "v3_filled": v3.get(n, {}).get("filled"),
                         "unbound_reported": v3.get(n, {}).get("unbound"), "gate_ready": truth.get(n, {}).get("gate_ready"),
                         "consensus_ok": truth.get(n, {}).get("consensus_ok")} for n in (1902, 1904)}
    return {"cycle": cycle, "prs_compared": len(both), "false_ready": false_ready, "divergence": divergence,
            "unreported_divergence": unreported, "controls": controls,
            "truth_only": sorted(set(truth) - set(v3)), "v3_only": sorted(set(v3) - set(truth))}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("truth_dir", type=Path)
    parser.add_argument("cycle")
    parser.add_argument("inbox_dir", type=Path)
    parser.add_argument("--label", default="")
    args = parser.parse_args(argv)
    result = {"label": args.label, **compare(args.truth_dir, args.cycle, args.inbox_dir)}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
