"""Cut a new frozen tournament round on a fixed interval: capture, lesson cards, freeze, render the own views.

usage: python -B sandbox/mind/capture/round_loop.py --bin <pinned .agent-bridge/bin> --data-root <own sandbox dir>
           --notes <lesson notes dir> --questions-dir <dir with questions.round-N.json> --first <N>
           --seed-from <earlier frozen capture.json> [--interval 1800] [--rounds K] [--start-delay S]
           [--variants v0_board,...]

Operator 10:17Z: a new frozen round every 30 minutes, whether or not every lane has scored. Each cycle runs, as
separate processes of this code tree, ``capture/capture_round.py`` (the external capture step), ``lesson_cards.py``
(as-of the capture time), ``freeze_inputs.py`` (round ``round-<N>``, offline from there on) and
``tournament_driver.py`` for each own variant. The question set is ``questions.round-<N>.json`` of the questions
dir, or the newest earlier ``questions.round-<M>.json`` when the round has none yet; the frozen manifest records its
name and sha256. Each round seeds its capture from the previous round's frozen capture.

With ``--from-live <live dir>`` (Lead 10:50Z: one producer, no duplicate), a round makes no capture of its own:
it freezes the cycle that ``<live dir>/latest.json`` names when its status is ``ok`` and its capture is younger
than ``--max-live-age`` seconds; otherwise the round is refused (a gap), never captured twice.

Prints one JSON line per cycle (the round, its directories and digests, or the step that refused) and goes on with
the next cycle after a refusal: a failed round is a gap, never a rewrite. Writes nothing itself; never writes a
bridge event (the lane posts each round announcement itself).
"""

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIND = HERE.parent
QUESTIONS = re.compile(r"questions\.round-([0-9]+)\.json")


def stamp():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def step(name, *command):
    done = subprocess.run([sys.executable, "-B", *map(str, command)], capture_output=True, timeout=1500)
    lines = done.stdout.decode("utf-8", "replace").strip().splitlines()
    if done.returncode != 0:
        raise RuntimeError(f"{name}_exit_{done.returncode}")
    return lines


def question_set(directory, number):
    found = {int(QUESTIONS.fullmatch(p.name).group(1)): p for p in directory.iterdir() if QUESTIONS.fullmatch(p.name)}
    usable = [n for n in found if n <= number]
    return found[max(usable)] if usable else None


def live_cycle(args):
    """The cycle dir the live producer last published, if ok and fresh enough; else a refusal."""
    latest = json.loads((args.from_live / "latest.json").read_text(encoding="ascii"))
    if latest.get("status") != "ok":
        raise RuntimeError("live_not_ok")
    observed = dt.datetime.strptime(latest["observed_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    if (dt.datetime.now(dt.timezone.utc) - observed).total_seconds() > args.max_live_age:
        raise RuntimeError("live_too_old")
    cycle = args.from_live.parent / latest["cycle_dir"]
    if not (cycle / "capture.json").exists():
        raise RuntimeError("live_capture_pruned")
    return cycle


def one_round(args, number, seed):
    name = f"round-{number}"
    if args.from_live is not None:
        cycle = live_cycle(args)
    else:
        cycle = args.data_root / "captures" / f"{name}-{stamp()}"
        step("capture", HERE / "capture_round.py", "--bin", args.bin, "--out", cycle, "--files-cache",
             args.files_cache, "--seed-from", seed, "--with-status")
    observed = json.loads((cycle / "capture.json").read_text(encoding="utf-8"))["observed_utc"]
    cards = args.data_root / "tournament" / "side" / f"lesson-cards.{name}.json"
    step("lesson_cards", MIND / "lesson_cards.py", "--notes", args.notes, "--as-of", observed, "--out", cards)
    questions = question_set(args.questions_dir, number)
    freeze = [MIND / "freeze_inputs.py", "--cycle-dir", cycle, "--data-root", args.data_root, "--round", name]
    step("freeze", *freeze, *(["--questions", questions] if questions else []))
    round_dir = args.data_root / "tournament" / ".codex-audit" / name
    views = {}
    for variant in args.variants.split(","):
        path = MIND / "variants" / f"{variant}.py"
        step("render_" + variant, MIND / "tournament_driver.py", "--round-dir", round_dir, "--variant", path,
             "--data-root", args.data_root, "--side-input", cards)
        code = re.match(r"v([0-9])_", variant).group(1)
        manifest = args.data_root / "tournament" / "views" / name / f"V{code}" / "views-manifest.json"
        views[f"V{code}"] = sha(manifest)
    inputs = json.loads((round_dir / "inputs-manifest.json").read_text(encoding="utf-8"))
    return round_dir / "capture.json", {
        "round": name, "round_dir": str(round_dir), "inputs_manifest_sha256": sha(round_dir / "inputs-manifest.json"),
        "capture_sha256": inputs["files"]["capture.json"], "context_sha256": inputs["files"]["context.json"],
        "observed_utc": observed, "events": inputs["capture"]["window"]["events"],
        "row_sources": inputs["capture"]["window"]["row_sources"],
        "snapshot_sha256": inputs["v0"]["snapshot_sha256"], "round_builder_sha256": inputs["round_builder_sha256"],
        "builder_head": inputs["builder"]["head"], "questions": inputs.get("questions"),
        "lesson_cards_sha256": sha(cards), "views_manifest_sha256": views}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bin", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--questions-dir", type=Path, required=True)
    parser.add_argument("--first", type=int, required=True)
    parser.add_argument("--seed-from", type=Path, required=True)
    parser.add_argument("--files-cache", type=Path, default=None)
    parser.add_argument("--interval", type=int, default=1800)
    parser.add_argument("--rounds", type=int, default=24)
    parser.add_argument("--start-delay", type=int, default=0, help="seconds before the first cycle")
    parser.add_argument("--from-live", type=Path, default=None,
                        help="the live producer's dir (with latest.json): freeze its last good cycle, no own capture")
    parser.add_argument("--max-live-age", type=int, default=300)
    parser.add_argument("--variants", default="v0_board,v4_pr_cards,v6_lessons_room")
    args = parser.parse_args()
    args.files_cache = args.files_cache or args.data_root / "captures" / "files-cache"
    seed = args.seed_from
    time.sleep(max(0, args.start_delay))
    for number in range(args.first, args.first + args.rounds):
        started = time.monotonic()
        try:
            seed, line = one_round(args, number, seed)
        except Exception as exc:  # a refused round is a gap; the next cycle starts on time
            line = {"round": f"round-{number}", "refused": str(exc)[:120]}
        print(json.dumps(dict(line, at=stamp())), flush=True)
        time.sleep(max(0, args.interval - (time.monotonic() - started)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
