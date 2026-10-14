"""Frozen inputs of hex-Mind step 1a: the P1 cycle copies, verified against their manifest before any use.

Reads only the frozen directory. ``manifest.json`` lists, per cycle, the sha256 of each file the copy holds; one
file whose bytes do not match aborts the whole load. A null entry (a capture the live producer had already pruned
before the copy) is reported and never read.

Event bodies: a vote row of the core snapshot names its event only by its content ref (the sha256 of the canonical
event, ``tools.bridge_compact_view.event_id``). The cycles whose capture was pruned keep the snapshot but not the
bodies, so the bodies come from every verified capture of the manifest, indexed by their recomputed ref. The lane
view looks a body up only for a ref the cycle's own snapshot names, so no later event enters an earlier cycle; the
ref binds the content whichever capture holds it, and the view checks each body against its ref again.

As-of: a cycle is complete at the last of its three observations (the bridge capture, the GitHub PR list, the
GitHub status list). A cycle that captured GitHub fresh read it seconds after the bridge (23:01:00Z bridge, 23:01:20Z
status in 20261013T230057Z), so that time, not the bridge time alone, is the as-of passed to the view.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any

SRC = Path(__file__).resolve().parents[3]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from tools import bridge_mind_workspace as ws  # noqa: E402
from tools import wd_bridge_mind as core  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402
from waggledance.core.bridge_log_reader import parse_bridge_json_object  # noqa: E402

FROZEN_ROOT = Path(r"F:\waggledance_memory\sandbox\fable-5\hexmind\frozen\p1-cycles-20261014")
MANIFEST_SCHEMA = "wd.mind-frozen-cycles.v1"
PUBLICATION = "run\\reflected.publication.json"
GITHUB_FILES = ("context.json", "observed-status.json", "gh-pr-list.json", "gh-open-status.json")
OBJECT_FILES = frozenset({"capture.json", "context.json", "observed-status.json", PUBLICATION})


class FrozenInputError(RuntimeError):
    """Fixed reasons that never echo file content."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def _no_constant(_):
    raise ValueError("non-finite number")


def parse(data: bytes, name: str) -> Any:
    """One frozen JSON file: objects through the canonical bridge reader, the raw gh lists through a strict loader."""
    try:
        text = data.decode("utf-8")
        if name in OBJECT_FILES:
            return parse_bridge_json_object(text)
        return json.loads(text, object_pairs_hook=_unique, parse_constant=_no_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise FrozenInputError(f"not one strict JSON value: {name}") from None


def _path(root: Path, cycle: str, name: str) -> Path:
    return root.joinpath(cycle, *name.split("\\"))


def load_manifest(root: Path = FROZEN_ROOT) -> tuple[dict[str, Any], str]:
    data = (root / "manifest.json").read_bytes()
    manifest = parse_bridge_json_object(data.decode("utf-8"))
    if manifest.get("schema") != MANIFEST_SCHEMA or not isinstance(manifest.get("cycles"), list):
        raise FrozenInputError("the manifest is not a wd.mind-frozen-cycles.v1 list of cycles")
    return manifest, sha256_bytes(data)


def read_verified(root: Path, entry: dict[str, Any]) -> tuple[dict[str, Any], list[str], dict[str, str]]:
    """The parsed files of one cycle whose bytes match the manifest, the names listed as null, and their sha256."""
    files, nulls, digests = {}, [], {}
    for name, digest in sorted(entry["files"].items()):
        if digest is None:
            nulls.append(name)
            continue
        data = _path(root, entry["cycle"], name).read_bytes()
        if sha256_bytes(data) != digest:
            raise FrozenInputError(f"sha256 mismatch: {entry['cycle']}/{name}")
        files[name], digests[name] = parse(data, name), digest
    return files, nulls, digests


def read_publication(publication: Any) -> dict[str, Any]:
    """The core snapshot of a publication envelope, checked the way the core's ``read_snapshot`` checks it."""
    if not isinstance(publication, dict) or set(publication) != {"schema", "snapshot", "snapshot_sha256"} \
            or publication["schema"] != "wd.bridge-mind-publication.v1":
        raise FrozenInputError("the publication envelope is invalid")
    snapshot = publication["snapshot"]
    try:
        core._validate_snapshot(snapshot)
    except core.ProjectionError:
        raise FrozenInputError("the core refuses the published snapshot") from None
    if sha256_bytes(core.canonical_bytes(snapshot)) != publication["snapshot_sha256"]:
        raise FrozenInputError("the snapshot does not match its published digest")
    return snapshot


def cycle_as_of(snapshot: dict[str, Any], github: dict[str, Any] | None) -> str:
    """The last of the cycle's observation times, in whole seconds (UTC)."""
    times = [snapshot["source"]["as_of_utc"]]
    if github is not None:
        times += [github["context"].get("observed_utc"), github["status"].get("observed_utc")]
    parsed = [ws.parse_time(value) for value in times]
    if None in parsed:
        raise FrozenInputError("a cycle observation time is not an explicit UTC time")
    return max(parsed).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_frozen(root: Path = FROZEN_ROOT) -> dict[str, Any]:
    """Every cycle of the manifest, verified, with the shared body store built from the verified captures."""
    manifest, manifest_sha = load_manifest(root)
    cycles, captures, bodies = [], [], {}
    for entry in manifest["cycles"]:
        files, nulls, digests = read_verified(root, entry)
        capture = files.pop("capture.json", None)
        if capture is not None:
            events = capture.get("events") if isinstance(capture, dict) else None
            if not isinstance(events, list) or not all(isinstance(event, dict) for event in events):
                raise FrozenInputError("a capture has no list of events")
            for event in events:
                bodies.setdefault(event_id(event), event)
            captures.append({"cycle": entry["cycle"], "sha256": digests["capture.json"], "events": len(events)})
        snapshot = read_publication(files[PUBLICATION])
        github = None
        if all(name in files for name in GITHUB_FILES):
            github = {"context": files["context.json"], "status": files["observed-status.json"],
                      "pr_list": files["gh-pr-list.json"], "open_status": files["gh-open-status.json"],
                      "pr_list_sha256": digests["gh-pr-list.json"], "open_status_sha256": digests["gh-open-status.json"]}
        cycles.append({"cycle": entry["cycle"], "snapshot": snapshot, "github": github, "bodies": bodies,
                       "as_of_utc": cycle_as_of(snapshot, github), "files": digests, "nulls": nulls})
    return {"manifest": manifest, "manifest_sha256": manifest_sha, "cycles": cycles, "captures": captures,
            "bodies": bodies}
