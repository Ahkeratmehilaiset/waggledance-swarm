"""Acceptance of hex-Mind step 1a on the frozen P1 cycles: the labelled PR1905 case, its must_not items and six
negative controls, per cycle.

usage: python -B sandbox/mind/step1/run_acceptance_1a.py [--frozen-root DIR] [--out DIR]

Inputs (read-only, verified first): the frozen cycles of ``manifest.json`` (``lane_view_frozen``), the labelled case
``p1-case-pr1905-v1.json`` (written before any P1 code; its ``expected`` and ``must_not`` sections are the
acceptance) and ``baseline-pr1905-v1.json`` (the old lenses). Outputs, only under ``--out``:

* ``views/<cycle>.lane-view.json`` and ``views/<cycle>.lanes.txt``: the view and every lane's few lines;
* ``acceptance-1a.json``: per cycle the positive check, the must_not items and the controls, with the sha256 of
  every input file, of the code and of every output file.

Negative controls are in-memory mutants of the frozen inputs (``lane_view_mutants``); no file is ever edited.
Deterministic: no clock is read, so a rerun writes the same bytes. Exit 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import lane_view as lv  # noqa: E402
import lane_view_frozen as fz  # noqa: E402
import lane_view_mutants as mut  # noqa: E402

SCHEMA = "wd.mind-step1a-acceptance.v1"
OUT_ROOT = Path(r"F:\waggledance_memory\sandbox\fable-5\hexmind\step1")
CASE_FILE = "p1-case-pr1905-v1.json"
BASELINE_FILE = "baseline-pr1905-v1.json"
CODE_FILES = ("lane_view.py", "lane_view_frozen.py", "lane_view_mutants.py", "run_acceptance_1a.py", "test_lane_view.py")
# Never in any output string: a merge-readiness claim or the manual_execution wording (case must_not 2).
FORBIDDEN = (re.compile(r"\bready\b", re.IGNORECASE), re.compile(r"merge eligible", re.IGNORECASE),
             re.compile(r"manual_execution", re.IGNORECASE))
ROUTE_WORDS = "operator manual train; the vote is still required"
AUTHOR_LANES = ("claude-rco-2", "fable-5")  # the case's commit authors that are not build slots
CONTROLS = ("a", "b", "c", "d", "e", "f")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def pr_lines(view: dict[str, Any], number: int) -> dict[str, list[str]]:
    """Every action line naming the PR, per lane."""
    tag = f"PR{number} "
    return {lane: [line for line in entry["actions"] if tag in line] for lane, entry in view["lanes"].items()}


def run_view(case: dict[str, Any]) -> dict[str, Any]:
    return lv.lane_view(case["snapshot"], case["github"], case["bodies"], as_of_utc=case["as_of_utc"])


class Labels:
    """The labelled case, read once: its facts, its expected line and its must_not items."""

    def __init__(self, case: dict[str, Any]):
        facts, expected = case["facts_in_input"], case["expected"]
        self.number = int(re.search(r"PR([0-9]+) ", expected["top_action"]).group(1))
        self.lane = expected["owning_lane"]
        self.head, self.old_head = facts["github_head"], facts["bridge_view_head"]
        self.old_request = facts["older_request"]["ref"]
        self.votes = {vote["agent"]: vote["ref"] for vote in facts["votes_at_github_head"]}
        self.authors = sorted(facts["commit_authors"])
        self.expected = expected


def positive(view: dict[str, Any], labels: Labels) -> dict[str, Any]:
    """The owning lane's line for the PR, exactly as the case expects it."""
    record = view["prs"][str(labels.number)]
    lines = view["lanes"][labels.lane]["actions"]
    mine = pr_lines(view, labels.number)[labels.lane]
    line = mine[0] if mine else ""
    expected = labels.expected
    checks = {
        "one_line_for_the_pr": len(mine) == 1,
        "first_line_of_the_lane": bool(lines) and lines[0] == line,
        "top_action": expected["top_action"] in line,
        "must_cite": all(cite in line for cite in expected["must_cite"]),
        "waiting_since": f"waiting since {expected['waiting_since_utc']}" in line,
        "route_wording": ROUTE_WORDS in line,
        "coverage_wording": view["coverage"] != lv.UNKNOWN
        or expected["coverage_wording_if_request_inventory_bounded"] in line,
        "record": record.get("state") == "action" and record.get("github_head") == labels.head
        and record.get("empty_slots") == ["build_tools"]
        and sorted(vote["ref"] for vote in record.get("credited", [])) == sorted(labels.votes.values()),
    }
    return {"pass": all(checks.values()), "checks": checks, "line": line}


def must_not(view: dict[str, Any], labels: Labels, mutant_a: dict[str, Any]) -> dict[str, bool]:
    """The case's four must_not items; the fourth on control (a)."""
    record = view["prs"][str(labels.number)]
    every_line = [line for entry in view["lanes"].values() for line in entry["actions"]]
    credited = record.get("credited", [])
    lines = pr_lines(view, labels.number)
    named = [line for found in lines.values() for line in found]
    return {
        "1_no_credit_for_old_head_or_request": record.get("github_head") == labels.head
        and all(vote["head"] == labels.head and vote["ref"] != labels.old_request for vote in credited)
        and not any(labels.old_head[:12] in line or labels.old_request[:16] in line for line in every_line),
        "2_no_ready_or_merge_eligible": not any(pattern.search(text) for pattern in FORBIDDEN
                                                for text in strings(view)),
        "3_no_author_named_for_the_rco_slot": not any(lines.get(lane) for lane in AUTHOR_LANES)
        and not any(lane in line for lane in AUTHOR_LANES for line in named)
        and not set(AUTHOR_LANES) & set(record.get("empty_slots", []))
        and not set(AUTHOR_LANES) & {vote["agent"] for vote in credited},
        "4_no_line_when_checks_not_all_success": not any(pr_lines(mutant_a, labels.number).values()),
    }


def _abstained(view: dict[str, Any], number: int) -> dict[str, Any] | None:
    return next((item for item in view["abstained"] if item["pr"] == number), None)


def controls(case: dict[str, Any], labels: Labels) -> tuple[dict[str, Any], dict[str, Any]]:
    """The six negative controls on one cycle. Returns their results and the view of control (a)."""
    number, branch = labels.number, case["github"]["context"]["prs"][str(labels.number)]["head_ref_name"]
    refs = sorted(labels.votes.values())
    out = {}

    view_a = run_view(mut.check_result(case, number, "unified", "FAILURE"))
    found = _abstained(view_a, number)
    out["a"] = {"pass": not any(pr_lines(view_a, number).values()) and found is not None
                and "required_check_not_success" in found["reasons"] and found["detail"].startswith("UNKNOWN"),
                "what": "required check unified FAILURE at the GitHub head",
                "detail": None if found is None else found["detail"]}

    moved, renamed = mut.rebind_events(case, number, refs, task=branch + "-moved")
    view = run_view(moved)
    record = view["prs"][str(number)]
    out["b"] = {"pass": not any(pr_lines(view, number).values())
                and not {vote["ref"] for vote in record.get("credited", [])} & (set(refs) | set(renamed.values()))
                and sorted(row["reason"] for row in record.get("not_credited", []) if row["ref"] in renamed.values())
                == ["other_task", "other_task"],
                "what": "both votes on another task id", "state": record["state"]}

    moved, renamed = mut.rebind_events(case, number, refs, head=labels.old_head)
    view = run_view(moved)
    record = view["prs"][str(number)]
    out["c"] = {"pass": not any(pr_lines(view, number).values()) and not record.get("credited")
                and sorted(row["reason"] for row in record.get("not_credited", []) if row["ref"] in renamed.values())
                == ["other_head", "other_head"],
                "what": "both votes re-bound to the bridge view head", "state": record["state"]}

    lane = next(row for row in case["snapshot"]["lanes"].values() if row["agent"] == labels.lane)
    gh = lv.read_github(case["github"], lv.ws.parse_time(case["as_of_utc"]))[0]
    late = {"agent": labels.lane, "agent_uuid": lane["agent_uuid"], "status": "build_consensus_pass",
            "head": labels.old_head, "task": branch}
    before, ref = mut.add_vote(case, number, ts_utc="2026-10-13T17:41:30Z", **late)
    view = run_view(before)
    record = view["prs"][str(number)]
    line = (pr_lines(view, number)[labels.lane] or [""])[0]
    kept = record.get("github_head") == labels.head and record.get("state") == "action" \
        and [row["reason"] for row in record["not_credited"] if row["ref"] == ref] == ["other_head"] \
        and sorted(vote["ref"] for vote in record["credited"]) == refs \
        and labels.expected["top_action"] in line and ref[:16] not in line
    after_utc = lv.stamp(gh["observed"] + (lv.ws.parse_time(case["as_of_utc"]) - gh["observed"]) / 2)
    after, _ = mut.add_vote(case, number, ts_utc=after_utc, **late)
    view = run_view(after)
    found = _abstained(view, number)
    out["d"] = {"pass": kept and not any(pr_lines(view, number).values()) and found is not None
                and "github_older_than_bridge_head" in found["reasons"],
                "what": "head A, then head B, then a late vote at A: B kept, the A vote stale; the same late vote "
                        "after the GitHub capture abstains",
                "late_vote_utc": ["2026-10-13T17:41:30Z", after_utc]}

    view = run_view(mut.drop_github(case))
    found = _abstained(view, number)
    out["e"] = {"pass": view["github"]["usable"] is False and found is not None
                and found["reasons"] == ["github_capture_missing"]
                and not any(entry["actions"] for entry in view["lanes"].values()),
                "what": "GitHub capture removed", "detail": None if found is None else found["detail"]}

    results = []
    for verification in ("unverified", None):
        view = run_view(mut.set_verification(case, number, labels.votes["codex-lead-1"], verification))
        record = view["prs"][str(number)]
        results.append(labels.votes["codex-lead-1"] not in {vote["ref"] for vote in record.get("credited", [])}
                       and "vote_row_refused" in record["reasons"] and not any(pr_lines(view, number).values()))
    out["f"] = {"pass": all(results), "what": "the Lead vote with a valid uuid and verification unverified, "
                                              "then missing", "variants": results}
    return out, view_a


def case_facts(case: dict[str, Any], view: dict[str, Any], labels: Labels) -> dict[str, bool]:
    """The labelled facts against this cycle's own inputs."""
    context = case["github"]["context"]["prs"][str(labels.number)]
    return {"github_head": context["head"] == labels.head,
            "bridge_view_head": case["snapshot"]["prs"][str(labels.number)]["head"] == labels.old_head,
            "commit_authors": sorted(context["commit_authors"] or []) == labels.authors,
            "checks": view["prs"][str(labels.number)].get("checks") == "6/6"}


def write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--frozen-root", type=Path, default=fz.FROZEN_ROOT)
    parser.add_argument("--out", type=Path, default=OUT_ROOT)
    args = parser.parse_args(argv)
    frozen = fz.load_frozen(args.frozen_root)
    label_bytes = (args.frozen_root / CASE_FILE).read_bytes()
    labels = Labels(json.loads(label_bytes.decode("utf-8")))
    baseline = json.loads((args.frozen_root / BASELINE_FILE).read_text(encoding="utf-8"))
    rows = []
    for case in frozen["cycles"]:
        view = run_view(case)
        texts = "\n".join(lv.render_text(view, lane) for lane in lv.LANES)
        view_path = args.out / "views" / f"{case['cycle']}.lane-view.json"
        text_path = args.out / "views" / f"{case['cycle']}.lanes.txt"
        view_sha = write(view_path, json.dumps(view, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
        text_sha = write(text_path, texts)
        result, view_a = controls(case, labels)
        rows.append({"cycle": case["cycle"], "as_of_utc": case["as_of_utc"], "inputs": case["files"],
                     "manifest_nulls": case["nulls"], "case_facts": case_facts(case, view, labels),
                     "outputs": {"view": {"path": str(view_path), "sha256": view_sha},
                                 "lanes_text": {"path": str(text_path), "sha256": text_sha}},
                     "positive": positive(view, labels), "must_not": must_not(view, labels, view_a),
                     "controls": result, "counts": view["counts"]})
    total = len(rows)
    summary = {
        "cycles": total,
        "positive": f"{sum(row['positive']['pass'] for row in rows)}/{total}",
        "must_not": {name: f"{sum(row['must_not'][name] for row in rows)}/{total}" for name in rows[0]["must_not"]},
        "controls": {name: f"{sum(row['controls'][name]['pass'] for row in rows)}/{total}" for name in CONTROLS},
        "case_facts": f"{sum(all(row['case_facts'].values()) for row in rows)}/{total}",
        "baseline": {"v4_top_names_pr1905": f"{baseline['v4_top_names_pr1905']}/{baseline['cycles']}",
                     "v3_names_pr1905_action": f"{baseline['v3_names_pr1905_action']}/{baseline['cycles']}",
                     "v3_ci_text_green": f"{baseline['v3_ci_text_green']}/{baseline['cycles']}"},
    }
    summary["all_pass"] = all(row["positive"]["pass"] and all(row["must_not"].values()) and all(row["case_facts"].values())
                              and all(row["controls"][name]["pass"] for name in CONTROLS) for row in rows)
    report = {
        "schema": SCHEMA, "authority": "none", "scope": "sandbox acceptance, not a gate",
        "case_id": json.loads(label_bytes)["case_id"],
        "inputs": {
            "frozen_root": str(args.frozen_root),
            "manifest": {"sha256": frozen["manifest_sha256"], "baseline_commit": frozen["manifest"]["baseline_commit"]},
            "case": {"file": CASE_FILE, "sha256": hashlib.sha256(label_bytes).hexdigest()},
            "baseline": {"file": BASELINE_FILE, "sha256": sha256_file(args.frozen_root / BASELINE_FILE)},
            "manifest_nulls": {row["cycle"]: row["manifest_nulls"] for row in rows if row["manifest_nulls"]},
            "body_store": {"captures": frozen["captures"], "refs": len(frozen["bodies"]),
                           "rule": "bodies by recomputed content ref from every verified capture; looked up only "
                                   "for refs the cycle's own snapshot names"},
            "code": {name: sha256_file(HERE / name) for name in CODE_FILES},
        },
        "summary": summary,
        "cycles": rows,
    }
    for text in strings(report):
        if lv.ws._has_marker(text):
            raise lv.LaneViewError("a guarded word is in the report; nothing is written")
    report_sha = write(args.out / "acceptance-1a.json", json.dumps(report, indent=1, sort_keys=True,
                                                                   ensure_ascii=False) + "\n")
    print(json.dumps({"acceptance": str(args.out / "acceptance-1a.json"), "sha256": report_sha, **summary}, indent=1))
    return 0 if summary["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
