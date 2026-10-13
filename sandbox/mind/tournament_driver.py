"""The common tournament driver: one frozen round, one variant module, one view set per lane; offline.

usage: python -B sandbox/mind/tournament_driver.py --round-dir <frozen round dir> --variant <variant .py>
           --data-root <own sandbox dir> [--lanes a,b,...]

Lead 09:17Z: every lane renders from the same frozen inputs, and the code that runs lives in a C git branch.

* The round (``freeze_inputs.py``): every file digest in ``inputs-manifest.json`` is checked, and the v0
  reflected snapshot is read with the core's ``read_snapshot`` and must have the manifest's snapshot digest.
* The variant: a tracked, clean ``.py`` file in a git work tree (its head and blob are recorded). It defines
  ``VARIANT`` (``V0``..``V9``) and ``render(inputs, lane) -> {file name: text}``, a pure function of
  ``inputs`` = ``{round, snapshot, snapshot_sha256, context, capture_sha256, context_sha256, events, side}``
  (``events``: the frozen capture rows), where
  ``side`` is the parsed ``--side-input`` JSON (a variant's own frozen data, such as lesson cards) or None; its
  sha256 is recorded. A variant that
  names gate code, a process, socket or HTTP module is refused before it is loaded (a hygiene scan, not a
  sandbox).
* Views: every text passes the workspace's decoded privacy guard and is written once to
  ``<data-root>/tournament/views/<round>/<VARIANT>/<lane>/``. ``views-manifest.json`` there names the inputs,
  the variant, and per lane each file's bytes, sha256 and the render time. Nothing reads the bridge, GitHub or a
  provider, and nothing writes a bridge event.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
from tools import bridge_mind_workspace as ws  # noqa: E402
from tools import wd_bridge_mind as core  # noqa: E402

LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
VARIANT_ID = re.compile(r"V[0-9]")
FILE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
# Names a variant must not contain: gate code (the sandbox hard rules) and process or network modules.
FORBIDDEN = ("verify_bridge_consensus", "check_bridge_changes_requested", "check_rco_pass_present",
             "idle_consensus_auto_merge", "rule12", "merge_executor", "receipt_executor", "subprocess", "socket",
             "urllib", "http.client", "requests", "os.system", "Popen")
VIEWS_SCHEMA = "wd.bridge-mind-tournament-views.v1"


class DriverRefused(RuntimeError):
    """Fixed reasons only; never input content."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load_round(round_dir):
    manifest_bytes = (round_dir / "inputs-manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes.decode("ascii"))
    for name, digest in manifest["files"].items():
        if sha256((round_dir / name).read_bytes()) != digest:
            raise DriverRefused("round_file_digest_mismatch")
    audit_root = round_dir.parent
    snapshot = core.read_snapshot(round_dir / "v0-run" / "reflected.publication.json", audit_root=audit_root)
    snapshot_sha = sha256(core.canonical_bytes(snapshot))
    if snapshot_sha != manifest["v0"]["snapshot_sha256"]:
        raise DriverRefused("round_snapshot_digest_mismatch")
    context = json.loads((round_dir / "context.json").read_bytes().decode("utf-8"))
    # The frozen capture rows (already digest-checked above), for variants that index events by task or request.
    events = json.loads((round_dir / "capture.json").read_bytes().decode("utf-8"))["events"]
    return manifest, sha256(manifest_bytes), {"events": events,
        "round": manifest["round"], "snapshot": snapshot, "snapshot_sha256": snapshot_sha, "context": context,
        "capture_sha256": manifest["files"]["capture.json"], "context_sha256": manifest["files"]["context.json"]}


def git(directory, *options):
    done = subprocess.run(["git", "-C", str(directory), *options], capture_output=True, timeout=60)
    if done.returncode != 0:
        raise DriverRefused("variant_not_tracked")
    return done.stdout.decode("utf-8").strip()


def load_variant(path):
    path = path.resolve()
    if path.suffix != ".py" or not path.is_file():
        raise DriverRefused("variant_not_a_python_file")
    tree = Path(git(path.parent, "rev-parse", "--show-toplevel"))
    relative = path.relative_to(tree.resolve()).as_posix()
    if git(tree, "status", "--porcelain", "--untracked-files=all", "--", relative):
        raise DriverRefused("variant_not_clean")
    blob = git(tree, "ls-files", "-s", "--error-unmatch", "--", relative).split(" ")[1]
    text = path.read_text(encoding="utf-8")
    if any(word in text for word in FORBIDDEN):
        raise DriverRefused("variant_names_forbidden_code")
    spec = importlib.util.spec_from_file_location("mind_variant_" + blob[:12], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not isinstance(getattr(module, "VARIANT", None), str) or not VARIANT_ID.fullmatch(module.VARIANT) \
            or not callable(getattr(module, "render", None)):
        raise DriverRefused("variant_interface_invalid")
    return module, {"path": relative, "tree_head": git(tree, "rev-parse", "HEAD"), "blob": blob,
                    "variant": module.VARIANT}


def render_lane(module, inputs, lane, out_dir):
    started = time.perf_counter()
    files = module.render(inputs, lane)
    elapsed = (time.perf_counter() - started) * 1000
    if not isinstance(files, dict) or not files or not all(
            isinstance(name, str) and FILE_NAME.fullmatch(name) and isinstance(text, str)
            for name, text in files.items()):
        raise DriverRefused("variant_output_invalid")
    ws.guard_object(files)
    out_dir.mkdir(parents=True)
    record = {"render_ms": round(elapsed, 2), "files": {}}
    for name in sorted(files):
        data = files[name].encode("utf-8")
        with open(out_dir / name, "xb") as handle:
            handle.write(data)
        record["files"][name] = {"bytes": len(data), "sha256": sha256(data)}
    record["bytes"] = sum(item["bytes"] for item in record["files"].values())
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--round-dir", type=Path, required=True)
    parser.add_argument("--variant", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--lanes", default=",".join(LANES))
    parser.add_argument("--side-input", type=Path, default=None)
    args = parser.parse_args()
    manifest, manifest_sha, inputs = load_round(Path(os.path.abspath(args.round_dir)))
    module, variant = load_variant(args.variant)
    side = None
    if args.side_input is not None:
        side_bytes = args.side_input.read_bytes()
        inputs["side"] = json.loads(side_bytes.decode("utf-8"))
        side = {"name": args.side_input.name, "sha256": sha256(side_bytes), "bytes": len(side_bytes)}
    else:
        inputs["side"] = None
    lanes = [lane for lane in args.lanes.split(",") if lane]
    if not lanes or not all(ws.LANE_ID.fullmatch(lane) for lane in lanes):
        raise DriverRefused("lanes_invalid")
    base = Path(os.path.abspath(args.data_root)) / "tournament" / "views" / manifest["round"] / variant["variant"]
    base.mkdir(parents=True)  # exclusive: one view set per round and variant; a new code version is a new round
    record = {"schema": VIEWS_SCHEMA, "authority": "none", "scope": "sandbox_tournament_views_not_gate",
              "round": manifest["round"], "inputs_manifest_sha256": manifest_sha,
              "capture_sha256": inputs["capture_sha256"], "context_sha256": inputs["context_sha256"],
              "snapshot_sha256": inputs["snapshot_sha256"],
              "round_builder_sha256": manifest["round_builder_sha256"], "variant": variant, "side_input": side,
              "lanes": {lane: render_lane(module, inputs, lane, base / lane) for lane in lanes}}
    ws.guard_object(record)
    with open(base / "views-manifest.json", "xb") as handle:
        handle.write((json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii"))
    print(json.dumps({"dir": str(base), "variant": variant["variant"],
                      "bytes": {lane: item["bytes"] for lane, item in record["lanes"].items()},
                      "render_ms": {lane: item["render_ms"] for lane, item in record["lanes"].items()}},
                     indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (DriverRefused, ws.WorkspaceError, core.ProjectionError) as exc:
        print(json.dumps({"refused": type(exc).__name__ + ": " + str(exc)}))
        sys.exit(2)
