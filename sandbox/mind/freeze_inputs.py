"""Freeze one tournament round's inputs: an already captured bridge window and PR context, imported offline.

usage: python -B sandbox/mind/freeze_inputs.py --cycle-dir <a finished producer cycle> --data-root <own sandbox dir>
           --round <name> [--questions <question set json>]

Lead 09:17Z B1-B3: the sandbox stays offline. This script reads no bridge, calls no GitHub, provider or gate,
and starts no process except local ``git`` to record the builder. It imports files that were already captured:

* ``capture.json`` (``wd.bridge-mind-capture.v1``, reader ``Read-AgentBridge.ps1 -Compact -EventId``) with its
  ``detail-manifest.txt``: the manifest sha256 must equal the capture's ``reader_stdout_sha256``, and every row's
  own canonical ref and row digest must equal its manifest line, in order. A ``seed`` row is bound to the
  compact reader's listed ref by that content hash.
* ``context.json`` (``wd.bridge-mind-pr-context.v1``/``v2``) with the ``gh-pr-list.json`` it was built from: the
  list's sha256 must equal the context's ``capture_sha256``, and the workspace must accept the context at the
  capture time.

The builder is the code tree this script runs from: its git head, clean for every Mind path, and the blob of each
Mind file. The frozen inputs go once into ``<data-root>/tournament/.codex-audit/<round>/`` (created exclusively,
never overwritten), with ``inputs-manifest.json`` naming every digest, the window and the builder, and ``v0-run/``:
the shadow pipeline over exactly those files. Nothing moves after that; a new round is a new directory.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
from tools import bridge_mind_workspace as ws  # noqa: E402
from tools import wd_bridge_mind_shadow as shadow  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402

MANIFEST_SCHEMA = "wd.bridge-mind-tournament-inputs.v1"
COMPACT_READER = "Read-AgentBridge.ps1 -Compact -EventId"
INPUT_FILES = ("capture.json", "detail-manifest.txt", "context.json", "gh-pr-list.json")
# Optional read-only GitHub status capture (required checks and merge state per open PR head), copied when present.
OPTIONAL_FILES = ("gh-open-status.json", "observed-status.json")
# The code a round's outputs depend on; each must be committed and clean in the builder tree.
MIND_PATHS = ("tools/wd_bridge_mind.py", "tools/bridge_mind_workspace.py", "tools/wd_bridge_mind_shadow.py",
              "tools/wd_bridge_mind_delta_push.py", "tools/wd_pr_missing_slot_board.py",
              "tools/bridge_compact_view.py", "waggledance/core/bridge_log_reader.py")
FREEZER = "sandbox/mind/freeze_inputs.py"
CHARTER_FILES = {"charter_blob": "docs/architecture/IDLE_AUTONOMY_CHARTER.md",
                 "classifier_blob": "tools/check_standing_consensus_sign_class.py"}


class FreezeRefused(RuntimeError):
    """Fixed reasons only; never input content."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def blob_sha1(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def git(*options):
    done = subprocess.run(["git", "-C", str(SRC), *options], capture_output=True, timeout=60)
    if done.returncode != 0:
        raise FreezeRefused("git_failed")
    return done.stdout.decode("utf-8").strip()


def row_digest(event):
    return sha256(json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def builder():
    if git("status", "--porcelain", "--untracked-files=all", "--", *MIND_PATHS, FREEZER):
        raise FreezeRefused("builder_not_clean")
    # The committed blob ids (git normalizes line endings; the tree is clean for these paths).
    files = {line.split("\t", 1)[1]: line.split(" ")[1]
             for line in git("ls-files", "-s", "--", *MIND_PATHS).splitlines()}
    # The round builder identity: the Mind code that produced the snapshot, not the tree head (a variant commit
    # moves the head but not the snapshot). Every result of one cohort names this same digest.
    identity = sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("ascii"))
    freezer = git("ls-files", "-s", "--error-unmatch", "--", FREEZER).split(" ")[1]
    return {"head": git("rev-parse", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"), "blobs": files,
            "round_builder_sha256": identity, "freezer_blob": freezer}


def check_capture(capture_bytes, manifest_bytes):
    capture = json.loads(capture_bytes.decode("utf-8"))
    shadow.read_capture(capture)
    if capture["reader"] != COMPACT_READER:
        raise FreezeRefused("capture_not_compact")
    if sha256(manifest_bytes) != capture["reader_stdout_sha256"]:
        raise FreezeRefused("manifest_digest_mismatch")
    lines = manifest_bytes.decode("ascii").split("\n")
    if len(lines) != len(capture["events"]):
        raise FreezeRefused("manifest_length_mismatch")
    sources = {}
    for line, event in zip(lines, capture["events"]):
        ref, digest, source = line.split(" ")
        if event_id(event) != ref or row_digest(event) != digest or source not in ("seed", "detail"):
            raise FreezeRefused("manifest_row_mismatch")
        sources[source] = sources.get(source, 0) + 1
    times = sorted(event.get("ts_utc") for event in capture["events"] if isinstance(event.get("ts_utc"), str))
    window = {"events": len(capture["events"]), "first_ref": event_id(capture["events"][0]),
              "last_ref": event_id(capture["events"][-1]), "first_ts_utc": times[0] if times else None,
              "last_ts_utc": times[-1] if times else None, "row_sources": sources}
    return capture, window


def check_context(context_bytes, list_bytes, as_of):
    context = json.loads(context_bytes.decode("utf-8"))
    if context.get("capture_sha256") != sha256(list_bytes):
        raise FreezeRefused("context_list_digest_mismatch")
    used, reason = ws.read_context(context, ws.parse_time(as_of))
    if used is None:
        raise FreezeRefused("context_refused_" + reason)
    charter = context["charter"]
    tree = {key: blob_sha1((SRC / path).read_bytes()) for key, path in CHARTER_FILES.items()}
    return context, {"schema": context["schema"], "observed_utc": context["observed_utc"],
                     "github_list_sha256": context["capture_sha256"], "prs": len(context["prs"]),
                     "open_prs": sum(1 for row in context["prs"].values() if row["state"] == "OPEN"),
                     "charter_loaded": charter["loaded"],
                     "charter_matches_builder": all(charter[key] == tree[key] for key in tree)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cycle-dir", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--round", required=True)
    parser.add_argument("--questions", type=Path, default=None,
                        help="the round's question set, copied as questions.json with its sha256")
    args = parser.parse_args()
    if not ws.TASK.fullmatch(args.round) or "/" in args.round:
        raise FreezeRefused("round_name_invalid")
    built = builder()
    data = {name: (args.cycle_dir / name).read_bytes() for name in INPUT_FILES}
    data.update({name: (args.cycle_dir / name).read_bytes() for name in OPTIONAL_FILES
                 if (args.cycle_dir / name).exists()})
    if args.questions is not None:
        questions = args.questions.read_bytes()
        ws.guard_object(json.loads(questions.decode("utf-8")))
        data["questions.json"] = questions
    capture, window = check_capture(data["capture.json"], data["detail-manifest.txt"])
    _, context = check_context(data["context.json"], data["gh-pr-list.json"], capture["observed_utc"])
    audit_root = Path(os.path.abspath(args.data_root)) / "tournament" / ".codex-audit"
    audit_root.mkdir(parents=True, exist_ok=True)
    out = audit_root / args.round
    out.mkdir()  # exclusive: a frozen round is never rewritten
    digests = {}
    for name, payload in data.items():
        with open(out / name, "xb") as handle:
            handle.write(payload)
        if sha256((out / name).read_bytes()) != sha256(payload):
            raise FreezeRefused("copy_mismatch")
        digests[name] = sha256(payload)
    receipt = shadow.run(out / "capture.json", audit_root, out / "v0-run", context_path=out / "context.json")
    manifest = {
        "schema": MANIFEST_SCHEMA, "authority": "none", "scope": "sandbox_tournament_inputs_not_gate",
        "round": args.round, "frozen_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "imported_from_cycle": args.cycle_dir.name, "files": digests,
        "questions": ({"file": "questions.json", "sha256": digests["questions.json"],
                       "source_name": args.questions.name} if args.questions is not None else None),
        "capture": {"sha256": digests["capture.json"], "reader": capture["reader"],
                    "reader_stdout_sha256": capture["reader_stdout_sha256"],
                    "observed_utc": capture["observed_utc"], "window": window,
                    "provenance": "pinned compact reader: -Compact -AfterEvent/-ContinuityTail listed the refs; "
                                  "detail rows came from -Compact -EventId, seed rows are earlier captured rows "
                                  "whose own canonical ref (sha256 of the canonical row) equals the listed ref"},
        "context": dict(context, sha256=digests["context.json"],
                        provenance="read-only GitHub metadata captured before this round (gh pr list, gh pr view "
                                   "files/commits); imported offline, never refreshed during the round"),
        "builder": built, "round_builder_sha256": built["round_builder_sha256"],
        "v0": {"dir": "v0-run", "snapshot_sha256": receipt["publication"]["snapshot_sha256"],
               "board_text_sha256": receipt["board_text_sha256"], "counts": receipt["counts"],
               "global_question": receipt["global_question"]},
        "read": "read v0-run/reflected.publication.json with tools/wd_bridge_mind.py read_snapshot(path, "
                "audit_root=<this .codex-audit>); every variant view is rendered from it and context.json only",
        "advisory": "observe only; before any vote or pass check the canonical tools",
    }
    ws.guard_object(manifest)
    with open(out / "inputs-manifest.json", "xb") as handle:
        handle.write((json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii"))
    print(json.dumps({"round": args.round, "dir": str(out), "capture": digests["capture.json"],
                      "context": digests["context.json"], "snapshot": manifest["v0"]["snapshot_sha256"],
                      "builder": built["head"], "round_builder_sha256": built["round_builder_sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (FreezeRefused, shadow.ShadowRefused) as exc:
        print(json.dumps({"refused": str(exc)}))
        sys.exit(2)
