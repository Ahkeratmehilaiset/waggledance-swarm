"""Default-off, bounded CLI candidate for the cooperative Q4 caller adapter.

No arguments emits OFF without reading environment, stdin, configuration or the
adapter. --enabled reads one JSON situation/time query exclusively from stdin.
The supplied time must lie within 300 seconds of the observed local UTC at the
start; that observed start ranks the query. Before output a backward local clock
refuses, and with a card binding every returned card must still be CURRENT at the
observed end. The local clock is read, never authenticated (no freshness claim).
WD_Q4_SHARED_DESCRIPTOR_PATH/SHA256 select the existing v2 descriptor. Its
bundle_root contains deployment-manifest.json, whose bytes must match the
externally supplied WD_REBOOT_EXPECTED_MANIFEST_HASH. The manifest's wd-fleet.json
pin must agree with the descriptor and actual fleet bytes. CALLER aliases are
not read. All referenced input bytes are bounded and checked against pins.

Identity is constructed in this process from AGENT_BRIDGE_AGENT, _AGENT_UUID,
_RUN_ID, the in-process hash of _OWNER_TOKEN, actual cwd and the pinned fleet
worktree. Its identity digest is SELF-COMPUTED. Same-user declarations and pins
are not authentication: lane_authentication must remain UNKNOWN. Ancestor/path
checks do not defeat hostile replacement after checking or same-user writers.

Complete pinned content-cell receipts expose a separate v2 delivery envelope:
index_age is wd.q4-cell-build-age.v1 with oldest/newest cell build UTC, floored
integer age seconds, the pinned content cutoff and its age, and observed local
UTC. Clock authentication, source freshness and coverage completeness remain
UNKNOWN. It is not a whole-memory commit timestamp. Missing optional time fields
preserve the exact legacy v1 UNKNOWN shape; malformed present metadata, unsafe
paths, pin mismatch or future times refuse. Old launchers refuse the new v2
shape: coordinated consumer changes are required before activation. No writer,
rebuild, descriptor-schema change, or production activation is added here.

Before entering a query and again before publishing its result, status/card plans
must still match their pinned pointer. A content plan has no legacy pointer: it
instead rechecks its exact plan, policy, roster, decision, descriptor, memory and
projection pins and encoder metadata. These are equality checks at two instants,
not authenticated time, source freshness, anti-ABA protection or a refresh.

An explicitly selected WD_Q4_LIVE_MODE=1 opts into a separate, mutable local
selector under the baseline descriptor's generation root. Each query rechecks
the candidate against the baseline bundle, fleet, runtime and policy bindings,
and refuses if the selector or index pointer changes before output. It does not
authenticate the selector's same-user publisher or lane and does not prove
wall-clock or source freshness. Missing or malformed live selectors refuse.

When the explicit P1 live route observes a well-formed interrupted publication,
it may instead use the already pinned baseline without touching the pending
marker or temporary file. This is a separate interrupted envelope: STALE means
that consumers must treat the baseline as potentially stale, not that the age
or the interruption's cause was authenticated. The baseline and the same
pending record are rechecked before output; all other selector failures refuse.
When a selector exists its validation checks consistency; it is not the source
of the answer, which remains the baseline. There is no automatic age limit or
expiry for this fallback: it can remain marked STALE until recovery reconciles
the interruption, and callers must not present it as current.

The enabled route must acquire an independently fleet-pinned guard and its
call-bound owner context before reading the query. Configuration and identity
reads still happen before guard acquisition and are not protected by it. The
adapter and native owners must each contribute actual cleanup to that context;
without all contributions, no result is published and the marker remains.
The existing adapter owns its native call and cleanup. This CLI adds no native
implementation, timeout watchdog, packaging, installed config or activation.
It requires the existing trusted bootstrap to expose the packaged tools module;
it never changes sys.path or guesses a dependency root. A missing chain refuses.

The caller must not automatically retry a nonzero exit: reconcile the failed
call first. Exit 3 means cleanup is UNKNOWN, 4 means timeout, 130 interruption,
and 2 another refusal. In particular, exit 3 requires an external no-retry hold
until cleanup is independently reconciled. These codes are signals, not cleanup
proof or a persistent cross-process guard. A separate caller-owned watchdog is
required to bound the entire invocation, including a stalled stdin read.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys


MAX_STDIN = 16000  # Same envelope as the existing lane-session CLI.
MAX_OUTPUT = 65536
MAX_DOCUMENT = 65536
MAX_MANIFEST = 1024 * 1024
MAX_FLEET = 1024 * 1024
MAX_SNAPSHOT = 16 * 1024 * 1024
MAX_RUNTIME = 8 * 1024 * 1024
MAX_INTERPRETER = 64 * 1024 * 1024
_HEX = re.compile(r"[0-9a-fA-F]{64}\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_REFUSAL = '{"error":"lane_caller_refused","authority":"none"}\n'
_LANES = frozenset({"codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"})
_DESCRIPTOR_KEYS = frozenset({
    "schema", "fleet_sha256", "lanes", "bundle_root", "public_plan", "interpreter", "cwd",
    "binding_path", "fixed_plan_path", "snapshot_path", "runtime_manifest_path",
    "interpreter_sha256", "binding_sha256", "fixed_plan_sha256", "snapshot_sha256",
    "runtime_manifest_sha256", "interpreter_max_bytes", "timeout_seconds", "max_output_bytes",
    "outer", "expected_binding",
})
_BINDING_KEYS = frozenset({"pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
                           "roster_sha256", "public_policy_sha256", "descriptor_sha256", "projection_sha256"})
# optional card route decision pin; when present it must also be in the public plan and the answer binding
_CARD_BINDING_KEYS = _BINDING_KEYS | {"card_decision_sha256"}
_STAGED_BINDING_KEYS = _CARD_BINDING_KEYS | {"card_policy_sha256"}
_CONTENT_BINDING_KEYS = frozenset({"encoder_sha256", "policy_sha256", "roster_sha256",
                                   "content_decision_sha256", "descriptor_sha256", "memory_sha256",
                                   "projection_sha256"})
_RESULT_KEYS = frozenset({"schema", "status", "lane", "lane_binding", "lane_authentication", "cleanup",
                          "inner_status", "result_count", "answer", "answer_sha256", "identity_sha256",
                          "descriptor_sha256", "call_id"})
_ANSWER_KEYS = frozenset({"schema", "lane", "status", "reason", "result_count", "results", "binding", "trust"})
_ITEM_KEYS = frozenset({"ref_sha256", "doc_id_sha256", "stored_text"})
_TRUST = {"lane_authentication": "UNKNOWN", "freshness": "UNKNOWN", "publisher_authentication": "UNKNOWN",
          "projection_public_attestation": "UNKNOWN", "transport": "caller_injected", "projection": "caller_pinned"}


def _check(condition):
    if not condition:
        raise ValueError()


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hex(value):
    _check(type(value) is str and _HEX.fullmatch(value) is not None)
    return value.lower()


def _same_digest(raw, expected):
    _check(hmac.compare_digest(_sha(raw), _hex(expected)))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _check(key not in result)
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError()


def _json(raw, keys=None):
    value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_unique,
                       parse_constant=_invalid_constant)
    _check(type(value) is dict and (keys is None or frozenset(value) == keys))
    return value


def _text(value, limit=32767):
    _check(type(value) is str and 0 < len(value) <= limit
           and not any(ord(char) < 32 or ord(char) == 127 for char in value))
    return value


def _env(name, limit=32767):
    return _text(os.environ.get(name), limit)


def _path(value):
    value = _text(value)
    path = Path(value)
    _check(path.is_absolute() and ".." not in path.parts and "." not in value.replace("\\", "/").split("/"))
    if os.name == "nt":
        _check(not value.startswith(("\\\\", "//")) and ":" not in value[2:])
    return path


def _read(path_value, limit, expected):
    path = _path(path_value)
    pin = _hex(expected)
    for part in (path, *path.parents):
        info = part.lstat()
        _check(not stat.S_ISLNK(info.st_mode)
               and not (getattr(info, "st_file_attributes", 0) & _REPARSE))
    info = path.stat()
    _check(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= limit)
    with path.open("rb") as stream:
        raw = stream.read(info.st_size + 1)
    _check(type(raw) is bytes and 0 < len(raw) <= limit and len(raw) == info.st_size)
    _same_digest(raw, pin)
    return raw


def _integer(value, ceiling):
    _check(type(value) is int and 0 < value <= ceiling)
    return value


def _binding(value):
    _check(type(value) is dict and frozenset(value) in (_BINDING_KEYS, _CARD_BINDING_KEYS, _STAGED_BINDING_KEYS, _CONTENT_BINDING_KEYS))
    return {key: _hex(value[key]) for key in sorted(value)}


def _configuration(descriptor_path=None, descriptor_sha256=None):
    if descriptor_path is None and descriptor_sha256 is None:
        descriptor_path = _env("WD_Q4_SHARED_DESCRIPTOR_PATH")
        descriptor_sha256 = _env("WD_Q4_SHARED_DESCRIPTOR_SHA256")
    descriptor_pin = _hex(descriptor_sha256)
    descriptor_raw = _read(descriptor_path, MAX_DOCUMENT, descriptor_pin)
    descriptor = _json(descriptor_raw, _DESCRIPTOR_KEYS)
    bundle_root = _path(descriptor["bundle_root"])
    manifest_raw = _read(str(bundle_root / "deployment-manifest.json"), MAX_MANIFEST,
                         _env("WD_REBOOT_EXPECTED_MANIFEST_HASH"))
    manifest = _json(manifest_raw)
    _check(type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1
           and type(manifest.get("files")) is dict)
    fleet_pin = _hex(manifest["files"].get("wd-fleet.json"))
    _check(descriptor["schema"] == "wd.q4-shared-session-config.v2"
           and _hex(descriptor["fleet_sha256"]) == fleet_pin)
    fleet_raw = _read(str(bundle_root / "wd-fleet.json"), MAX_FLEET, fleet_pin)
    _check(type(descriptor["lanes"]) is list and descriptor["lanes"]
           and all(type(lane) is str and lane in _LANES for lane in descriptor["lanes"])
           and len(set(descriptor["lanes"])) == len(descriptor["lanes"])
           and type(descriptor["outer"]) is dict)
    binding = _binding(descriptor["expected_binding"])
    interpreter_limit = _integer(descriptor["interpreter_max_bytes"], MAX_INTERPRETER)
    timeout = _integer(descriptor["timeout_seconds"], 600)
    output_limit = _integer(descriptor["max_output_bytes"], MAX_OUTPUT)
    for key in ("bundle_root", "public_plan", "cwd", "interpreter"):
        _path(descriptor[key])
    _read(descriptor["interpreter"], interpreter_limit, descriptor["interpreter_sha256"])
    inputs = {"fleet_bytes": fleet_raw, "expected_fleet_sha256": fleet_pin,
              "binding_bytes": _read(descriptor["binding_path"], MAX_DOCUMENT, descriptor["binding_sha256"]),
              "expected_binding_sha256": _hex(descriptor["binding_sha256"]),
              "bundle_root": descriptor["bundle_root"], "public_plan_path": descriptor["public_plan"],
              "executable": descriptor["interpreter"],
              "expected_interpreter_sha256": _hex(descriptor["interpreter_sha256"]),
              "interpreter_max_bytes": interpreter_limit,
              "fixed_plan_bytes": _read(descriptor["fixed_plan_path"], MAX_DOCUMENT, descriptor["fixed_plan_sha256"]),
              "expected_fixed_plan_sha256": _hex(descriptor["fixed_plan_sha256"]),
              "fixed_bundle_bytes": _read(descriptor["snapshot_path"], MAX_SNAPSHOT, descriptor["snapshot_sha256"]),
              "runtime_manifest_bytes": _read(descriptor["runtime_manifest_path"], MAX_RUNTIME,
                                               descriptor["runtime_manifest_sha256"]),
              "cwd": descriptor["cwd"], "timeout_seconds": timeout, "max_output_bytes": output_limit}
    return descriptor, descriptor_raw, descriptor_pin, fleet_raw, binding, inputs


def _require_current_snapshot(descriptor, binding, inputs):
    """Recheck pinned inputs before and after the call; no authenticated freshness claim."""
    from tools import wd_q4_lane_link as link
    binding_doc = _json(inputs["binding_bytes"])
    _check(binding_doc.get("schema") == "wd.q4-lane-binding.v1"
           and binding_doc.get("binding") == binding)
    public_hash = _hex(binding_doc.get("public_plan_sha256"))
    _, public_bytes, public_pins = link._public_plan(descriptor["public_plan"], public_hash)
    _check(("card_decision_sha256" in public_pins) == ("card_decision_sha256" in binding)
           and all(public_pins[key] == binding[key] for key in public_pins))
    public = _json(public_bytes)
    if frozenset(binding) == _CONTENT_BINDING_KEYS:
        # Content snapshots have no legacy pointer. Recheck every selected input
        # at both call boundaries; this proves byte consistency, not freshness.
        _check(public.get("schema") == link._CONTENT_PLAN_SCHEMA
               and frozenset(public_pins) == _CONTENT_BINDING_KEYS)
        config = public["config"]
        _check(type(config) is dict)
        memory = _path(config["memory"])
        descriptor_path = _path(config["descriptor"])
        _check(descriptor_path == memory / "descriptor.json")
        for name in ("policy", "roster", "content_decision"):
            limit = MAX_FLEET if name == "roster" else MAX_DOCUMENT
            _read(public[name + "_path"], limit, binding[name + "_sha256"])
        descriptor_raw = _read(str(descriptor_path), MAX_DOCUMENT, binding["descriptor_sha256"])
        memory_raw = _read(str(memory / "memory.json"), MAX_SNAPSHOT, binding["memory_sha256"])
        _read(str(memory / "projection.json"), MAX_SNAPSHOT, binding["projection_sha256"])
        selected = _json(descriptor_raw)
        manifest = _json(memory_raw)
        from sandbox.mind.step2.shared_index_preflight import _encoder_digest
        _check(frozenset(selected) == frozenset({"schema", "scope", "memory_sha256", "dim", "encoder"})
               and selected["schema"] == "wd.bridge-mind-q4-memory-descriptor.v2"
               and selected["scope"] == "wd.q4-shared-projected-events.v1"
               and type(selected["dim"]) is int and selected["dim"] > 0
               and selected["memory_sha256"] == binding["memory_sha256"]
               and _encoder_digest(selected["encoder"]) == binding["encoder_sha256"]
               and selected["encoder"]["revision"] == config["runtime_revision"]
               and manifest.get("projection_schema") == "wd.mind-content-memory-projection.v1"
               and manifest.get("projection_sha256") == binding["projection_sha256"])
        return _content_cell_age_snapshot(memory, manifest)
    staged = "card_policy_sha256" in binding
    _check((public.get("schema") == "wd.q4-public-wrapper-plan.v3") == staged)
    if staged:
        _read(str(_path(public["pointer_path"])), 1048576, binding["pointer_sha256"])
    else:
        root = _path(public["root"])
        _read(str(root / "q4-latest.json"), 16384, binding["pointer_sha256"])


def _age_utc(value):
    """Strict pinned UTC text, never a filesystem mtime or an authenticated clock."""
    _check(type(value) is str and _UTC.fullmatch(value) is not None)
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _content_cell_age_snapshot(memory, manifest):
    """Read metadata only from the already selected, hash-pinned content memory.

    Missing optional time fields mean no age capability, not an integrity waiver.
    Every present cell receipt is still bounded and fully hash-verified. This is
    not a replacement for the existing native/index/content verification chain.
    """
    cutoff = manifest.get("cutoff_utc")
    if "cutoff_utc" in manifest:
        _age_utc(cutoff)
    if "cells" not in manifest:
        return (cutoff, ()) if cutoff is not None else None
    from waggledance.core.hex_cell_topology import ALL_CELLS
    cells = manifest["cells"]
    _check(manifest.get("schema") == "wd.bridge-mind-hex-memory-step2.v1"
           and manifest.get("scope") == "wd.q4-shared-projected-events.v1"
           and type(cells) is dict and len(cells) <= len(ALL_CELLS))
    if not cells:
        return cutoff, ()
    captured = []
    for cell_id, pointer in sorted(cells.items()):
        _check(type(cell_id) is str and cell_id in ALL_CELLS and type(pointer) is dict
               and frozenset(pointer) == frozenset({"generation", "seq", "receipt_sha256", "count"}))
        generation = pointer["generation"]
        _check(type(generation) is int and 0 <= generation <= 999999
               and type(pointer["seq"]) is int and pointer["seq"] >= 0
               and type(pointer["count"]) is int and pointer["count"] >= 0)
        pin = _hex(pointer["receipt_sha256"])
        # Closed cell names and a bounded integer derive this path. _read also
        # refuses symlinks/reparse points at every ancestor and caps raw bytes.
        receipt_path = memory / "cells" / cell_id / ("gen-%06d" % generation) / "receipt.json"
        receipt = _json(_read(str(receipt_path), MAX_FLEET, pin))
        content = receipt.get("content")
        _check(receipt.get("schema") == "wd.bridge-mind-hex-cell.v1"
               and receipt.get("authority") == "none" and receipt.get("scope") == manifest["scope"]
               and type(content) is dict and content.get("cell_id") == cell_id
               and type(content.get("count")) is int and content["count"] == pointer["count"]
               and content.get("projection_sha256") == manifest["projection_sha256"])
        cell_cutoff = content.get("cutoff_utc")
        if "cutoff_utc" in content:
            _age_utc(cell_cutoff)
        if cutoff is not None:
            _check(cell_cutoff == cutoff)
        built = receipt.get("built_utc")
        if "built_utc" in receipt:
            _age_utc(built)
        captured.append((cell_id, generation, pin, built, cell_cutoff))
    return cutoff, tuple(captured)


def _content_cell_age_value(snapshot, observed):
    """Whole integer seconds, floored; local clock and source freshness UNKNOWN."""
    if snapshot is None:
        return {"status": "UNKNOWN", "seconds": None}
    _check(type(observed) is datetime and observed.tzinfo is not None
           and observed.utcoffset().total_seconds() == 0)
    cutoff, cells = snapshot
    dates = []
    if cutoff is not None:
        cutoff_time = _age_utc(cutoff)
        _check(cutoff_time <= observed)
    for _, _, _, built, cell_cutoff in cells:
        if cell_cutoff is not None:
            _check(_age_utc(cell_cutoff) <= observed)
        if built is not None:
            value = _age_utc(built)
            _check(value <= observed)
            dates.append((value, built))
    # Check all available times before fallback: missing metadata cannot hide
    # a future or malformed time on another cell. Never report a partial range.
    if not cells or cutoff is None or len(dates) != len(cells):
        return {"status": "UNKNOWN", "seconds": None}
    oldest, newest = min(dates), max(dates)
    def seconds(when):
        delta = observed - when
        return delta.days * 86400 + delta.seconds
    return {"schema": "wd.q4-cell-build-age.v1", "status": "OBSERVED",
            "basis": "hash_pinned_cell_receipts",
            "observed_local_utc": observed.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "clock_authentication": "UNKNOWN", "source_freshness": "UNKNOWN",
            "coverage_completeness": "UNKNOWN", "oldest_cell_built_utc": oldest[1],
            "newest_cell_built_utc": newest[1], "oldest_cell_age_seconds": seconds(oldest[0]),
            "newest_cell_age_seconds": seconds(newest[0]), "coverage_cutoff_utc": cutoff,
            "coverage_age_seconds": seconds(cutoff_time)}


def _with_content_cell_age(rendered, snapshot, observed, limit, token):
    """Version the closed content envelope; old launchers must refuse v2.

    Called only after the unchanged result/digest/cleanup validation, final pin
    rechecks and clock check. Missing age capability preserves exact v1 UNKNOWN.
    """
    value = _json(rendered.encode("ascii"))
    if value.get("schema") != "wd.q4-lane-content-delivery.v1":
        return rendered
    age = _content_cell_age_value(snapshot, observed)
    if age["status"] == "UNKNOWN":
        return rendered
    value["schema"] = "wd.q4-lane-content-delivery.v2"
    value["index_age"] = age
    raw = _canon(value) + b"\n"
    _check(len(raw) <= limit and token.encode("utf-8", "strict") not in raw
           and _canon(token)[1:-1] not in raw)
    return raw.decode("ascii")


def _interrupted_baseline_marker(live, descriptor_path, baseline):
    """Validate a pinned baseline and an unchanged, explicit P1 pending marker.

    The record is a cooperative observation, not publisher authentication.
    Neither the marker nor its temporary file is modified or interpreted as
    proof that publication succeeded.
    """
    descriptor, descriptor_raw, descriptor_pin, fleet_raw, _, inputs = baseline
    base = live.pointer_io._absolute(descriptor_path)
    _check(base.name == 'shared-session-config.v2.json'
           and live._GEN.fullmatch(base.parent.name) is not None
           and all(Path(descriptor[key]) == base.parent / name
                   for key, name in live._BASELINE_GENERATION_PATHS))
    root = base.parent.parent
    live._check_disk(descriptor, descriptor_raw, descriptor_pin, fleet_raw, inputs)
    public, _, _ = live._plan(descriptor, inputs)
    _check(frozenset(descriptor['expected_binding']) == _STAGED_BINDING_KEYS
           and public.get('schema') == 'wd.q4-public-wrapper-plan.v3')
    live._validate_roots(root, Path(descriptor['bundle_root']), Path(public['root']))
    marker_path = root / live._PUBLICATION_PENDING
    def signature(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    before = marker_path.lstat()
    raw = live._read(marker_path, 4096)
    after = marker_path.lstat()
    _check(stat.S_ISREG(before.st_mode) and signature(before) == signature(after))
    marker = _json(raw, frozenset({'schema', 'attempt', 'baseline_sha256',
                                   'previous_selector_sha256', 'publication_sha256', 'temporary_name'}))
    attempt = marker['attempt']
    previous = marker['previous_selector_sha256']
    publication = marker['publication_sha256']
    _check(_canon(marker) == raw and marker['schema'] == 'wd.q4-live-publication-pending.v1'
           and type(attempt) is str and re.fullmatch(r'[0-9a-f]{32}', attempt) is not None
           and marker['baseline_sha256'] == descriptor_pin
           and marker['temporary_name'] == '.q4-live-current-' + attempt + '.tmp'
           and type(publication) is str and live._HEX.fullmatch(publication) is not None
           and (previous is None or (type(previous) is str and live._HEX.fullmatch(previous) is not None))
           and previous != publication)
    target = root / live._POINTER
    if os.path.lexists(target):
        selected_raw = live._read(target, 4096)
        selected = _json(selected_raw, live._KEYS)
        _check(_sha(selected_raw) in (previous, publication) and selected['schema'] == live._SCHEMA
               and selected['baseline_sha256'] == descriptor_pin
               and type(selected['generation']) is str and live._GEN.fullmatch(selected['generation']) is not None
               and all(type(selected[key]) is str and live._HEX.fullmatch(selected[key]) is not None
                       for key in ('descriptor_sha256', 'index_pointer_sha256')))
        candidate_path = root / selected['generation'] / 'shared-session-config.v2.json'
        candidate = _configuration(str(candidate_path), selected['descriptor_sha256'])
        _check(all(Path(candidate[0][key]) == candidate_path.parent / name
                   for key, name in live._GENERATION_PATHS))
        index_root, (pointer_path, pointer_limit) = live._checked_pair(
            baseline, candidate, _hex(_env('WD_REBOOT_EXPECTED_MANIFEST_HASH')), p1_enabled=True)
        live._validate_roots(root, Path(descriptor['bundle_root']), Path(index_root))
        _check(candidate[0]['expected_binding']['pointer_sha256'] == selected['index_pointer_sha256'])
        live._read(pointer_path, pointer_limit, selected['index_pointer_sha256'])
        live._read(candidate_path, MAX_DOCUMENT, selected['descriptor_sha256'])
        _check(live._read(target, 4096) == selected_raw)
    else:
        _check(previous is None)
    return str(marker_path), raw, signature(after)


def _identity(fleet_raw, descriptor, descriptor_pin):
    lane = _env("AGENT_BRIDGE_AGENT", 32)
    agent_uuid = _env("AGENT_BRIDGE_AGENT_UUID", 36)
    run_id = _env("AGENT_BRIDGE_RUN_ID", 256)
    token = _env("AGENT_BRIDGE_OWNER_TOKEN", 4096)
    _check(lane in _LANES and lane in descriptor["lanes"] and _UUID.fullmatch(agent_uuid) is not None)
    fleet = _json(fleet_raw)
    _check(type(fleet.get("lanes")) is list and type(fleet.get("tools_supervisor")) is dict)
    entries = [*fleet["lanes"], fleet["tools_supervisor"]]
    _check(all(type(entry) is dict for entry in entries))
    matches = [entry for entry in entries if entry.get("agent") == lane]
    _check(len(matches) == 1 and matches[0].get("agent_uuid") == agent_uuid)
    worktree = _path(matches[0].get("worktree"))
    cwd = _path(os.getcwd())
    root_key, cwd_key = os.path.normcase(str(worktree)), os.path.normcase(str(cwd))
    _check(os.path.commonpath((root_key, cwd_key)) == root_key)
    identity = {"schema": "wd.q4-lane-caller-identity.v1", "agent": lane, "agent_uuid": agent_uuid,
                "worktree": str(worktree), "cwd": str(cwd), "caller_pid": os.getpid(), "run_id": run_id,
                "owner_token_sha256": _sha(token.encode("utf-8", "strict")),
                "descriptor_sha256": descriptor_pin}
    return lane, _canon(identity), token


def _query():
    raw = sys.stdin.buffer.read(MAX_STDIN + 1)
    _check(type(raw) is bytes and 0 < len(raw) <= MAX_STDIN)
    query = _json(raw, frozenset({"situation", "time"}))
    situation = query["situation"]
    _check(type(situation) is str and situation.strip() and len(situation.encode("utf-8", "strict")) <= MAX_STDIN)
    when = query["time"]
    _check(type(when) is str and _UTC.fullmatch(when) is not None)
    return situation, datetime.fromisoformat(when[:-1] + "+00:00")


def _utc_now():
    """Observed local UTC (read, never authenticated); a module seam for synthetic tests."""
    return datetime.now(timezone.utc)


def _output(result, lane, identity_pin, descriptor_pin, expected_binding, limit, token):
    _check(type(result) is dict and frozenset(result) == _RESULT_KEYS
           and result["schema"] == "wd.q4-lane-caller-result.v1" and result["status"] == "VALIDATED"
           and result["lane"] == lane and result["lane_binding"] == "DECLARED_CONSISTENT"
           and result["lane_authentication"] == "UNKNOWN" and result["cleanup"] == "chain_confirmed"
           and result["inner_status"] in ("ANSWER", "ABSTAIN", "DEGRADED")
           and result["identity_sha256"] == identity_pin and result["descriptor_sha256"] == descriptor_pin
           and type(result["call_id"]) is str and re.fullmatch(r"[0-9a-f]{32}", result["call_id"]) is not None)
    answer = result["answer"]
    _check(type(answer) is dict and frozenset(answer) == _ANSWER_KEYS
           and answer["schema"] == "wd.q4-lane-answer.v1" and answer["lane"] == lane
           and answer["status"] == result["inner_status"] and answer["reason"] == ""
           and type(answer["trust"]) is dict and answer["trust"] == _TRUST
           and _binding(answer["binding"]) == expected_binding
           and type(answer["results"]) is list and len(answer["results"]) <= 100
           and type(answer["result_count"]) is int and type(result["result_count"]) is int
           and answer["result_count"] == result["result_count"] == len(answer["results"]))
    _check(answer["status"] != "ANSWER" or answer["result_count"] > 0)
    _check(answer["status"] != "ABSTAIN" or answer["result_count"] == 0)
    for item in answer["results"]:
        _check(type(item) is dict and frozenset(item) == _ITEM_KEYS
               and type(item["stored_text"]) is str and len(item["stored_text"].encode("utf-8", "strict")) <= 320)
        _hex(item["ref_sha256"])
        _hex(item["doc_id_sha256"])
    _check(result["answer_sha256"] == _sha(_canon(answer)))
    raw = _canon(result) + b"\n"
    _check(len(raw) <= limit)
    # The closed schema and adapter's parent-resolved records exclude query
    # fields. Public record/metadata text may coincide with a query; tokens may not.
    _check(token.encode("utf-8", "strict") not in raw and _canon(token)[1:-1] not in raw)
    if (answer["status"] == "ANSWER" and answer["results"]
            and frozenset(expected_binding) == _CONTENT_BINDING_KEYS):
        # This is a caller-pinned content route, not authenticated freshness.
        # Age starts UNKNOWN here. Only final rechecked, complete cell receipts
        # may version this envelope; no record timestamp or filesystem mtime.
        delivered = {
            "schema": "wd.q4-lane-content-delivery.v1", "freshness": "UNKNOWN",
            "index_age": {"status": "UNKNOWN", "seconds": None},
            **{key: result[key] for key in ("lane", "lane_binding", "lane_authentication", "cleanup",
                                             "identity_sha256", "descriptor_sha256", "call_id",
                                             "answer_sha256")},
            "answer": answer,
        }
        raw = _canon(delivered) + b"\n"
        _check(len(raw) <= limit)
        _check(token.encode("utf-8", "strict") not in raw and _canon(token)[1:-1] not in raw)
    elif answer["results"]:
        # Every accepted trust tuple currently has UNKNOWN source freshness.
        # Keep the original result for final pin/card rechecks and guard release;
        # expose only labelled provenance, never its unverified record text.
        withheld = {
            "schema": "wd.q4-lane-content-withheld.v1", "status": "CONTENT_WITHHELD",
            "reason": "SOURCE_FRESHNESS_UNVERIFIED", "freshness": "UNKNOWN", "authority": "none",
            **{key: result[key] for key in ("lane", "lane_binding", "lane_authentication", "cleanup",
                                          "identity_sha256", "descriptor_sha256", "call_id")},
            "original_inner_status": result["inner_status"],
            "original_result_count": result["result_count"],
            "original_answer_sha256": result["answer_sha256"],
            "binding": answer["binding"], "trust": answer["trust"],
            "original_result_refs": [{key: item[key] for key in ("ref_sha256", "doc_id_sha256")}
                                     for item in answer["results"]],
        }
        raw = _canon(withheld) + b"\n"
        _check(len(raw) <= limit)
        _check(token.encode("utf-8", "strict") not in raw and _canon(token)[1:-1] not in raw)
    return raw.decode("ascii")


def _write(text):
    count = sys.stdout.write(text)
    _check(type(count) is int and count == len(text))
    sys.stdout.flush()


_DIAGNOSTIC_PHASES = frozenset({
    "CALLER_GUARD_ACQUIRE_UNKNOWN", "CALLER_ADAPTER_CLEANUP_UNKNOWN",
    "CALLER_TRANSPORT_CLEANUP_UNKNOWN", "CALLER_ATTESTATION_CLEANUP_UNKNOWN",
    "CALLER_SESSION_UNKNOWN", "CALLER_TIMEOUT_UNKNOWN", "CALLER_RESULT_UNKNOWN",
    "CALLER_TIMEOUT", "CALLER_INTERRUPTED", "CALLER_REFUSED",
})
_INNER_PHASES = {"transport_cleanup_unknown": "CALLER_TRANSPORT_CLEANUP_UNKNOWN",
                 "attestation_cleanup_unknown": "CALLER_ATTESTATION_CLEANUP_UNKNOWN",
                 "session_refused": "CALLER_SESSION_UNKNOWN", "timeout": "CALLER_TIMEOUT_UNKNOWN"}


def _refuse(code):
    # Preserve the nonzero outcome even when the diagnostic stream is unavailable.
    try:
        sys.stderr.write(_REFUSAL)
        sys.stderr.flush()
    except BaseException:
        pass
    return code


def _refuse_phase(code, phase):
    result = _refuse(code)
    try:
        if os.environ.get("WD_Q4_DIAGNOSTIC_PHASES") == "1" and phase in _DIAGNOSTIC_PHASES:
            sys.stderr.write('{"phase":"' + phase + '"}\n')
            sys.stderr.flush()
    except BaseException:
        pass
    return result


def main(argv=None):
    token = situation = identity_raw = inputs = result = None
    guard = owner_context = rendered = None
    guard_released = False
    live_publication = None
    interrupted_marker = None
    try:
        if argv is None:
            argv = sys.argv[1:]
        if argv == []:
            _write('{"status":"OFF"}\n')
            return 0
        _check(argv == ["--enabled"])
        descriptor, descriptor_raw, descriptor_pin, fleet_raw, binding, inputs = _configuration()
        live_mode = os.environ.get("WD_Q4_LIVE_MODE", "")
        p1_mode = os.environ.get("WD_Q4_LIVE_P1", "")
        coverage_mode = os.environ.get("WD_Q4_WITH_COVERAGE", "")
        _check(live_mode in ("", "1"))
        _check(p1_mode in ("", "1") and (p1_mode != "1" or live_mode == "1"))
        _check(coverage_mode in ("", "1") and (coverage_mode != "1" or live_mode == "1"))
        _check(p1_mode != "1" or coverage_mode == "")
        if live_mode == "1":
            from tools import wd_q4_live_snapshot_publish as live
            selection = dict(enabled=True, baseline_path=_env("WD_Q4_SHARED_DESCRIPTOR_PATH"),
                baseline_sha256=descriptor_pin,
                baseline=(descriptor, descriptor_raw, descriptor_pin, fleet_raw, binding, inputs),
                manifest_sha256=_hex(_env("WD_REBOOT_EXPECTED_MANIFEST_HASH")), load_configuration=_configuration)
            if p1_mode == "1":
                selection["p1_enabled"] = True
            base_path = live.pointer_io._absolute(selection['baseline_path'])
            pending_path = base_path.parent.parent / live._PUBLICATION_PENDING
            if p1_mode == "1" and os.path.lexists(pending_path):
                interrupted_marker = _interrupted_baseline_marker(
                    live, selection['baseline_path'], selection['baseline'])
            else:
                live_publication = live.select(**selection)
                descriptor, descriptor_raw, descriptor_pin, fleet_raw, binding, inputs = live_publication["configuration"]
        age_snapshot = _require_current_snapshot(descriptor, binding, inputs)
        lane, identity_raw, token = _identity(fleet_raw, descriptor, descriptor_pin)
        identity_pin = _sha(identity_raw)
        # The fleet hash has already been checked against the independently
        # anchored deployment manifest by _configuration, before this call.
        from tools import wd_q4_guard_completion as guard_completion
        try:
            guard = guard_completion.acquire_guard(
                fleet_bytes=fleet_raw, expected_fleet_sha256=inputs["expected_fleet_sha256"],
                lane=lane, identity_sha256=identity_pin, descriptor_sha256=descriptor_pin)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            # A failed acquisition may have left a pending marker; do not retry.
            return _refuse_phase(3, "CALLER_GUARD_ACQUIRE_UNKNOWN")
        owner_context = guard._owner_context()
        _check(guard._accept_owner_context(owner_context) is owner_context)
        try:
            situation, supplied = _query()
            # The observed start ranks the query; a supplied time over 300 s
            # away refuses. No clock authentication, no freshness claim.
            from tools import wd_q4_lane_query as lane_query
            when = lane_query.observed_lane_time(supplied, _utc_now())
            _content_cell_age_value(age_snapshot, when)
        except Exception:
            # Nothing has been handed off and no owner has entered. This is a
            # distinct cancellation witness, never a cleanup witness.
            witness = guard._cancel_unstarted(owner_context)
            guard.release(witness)
            guard_released = True
            raise
        # The trusted bootstrap supplies the existing package; never fall back.
        from tools import wd_q4_lane_caller_adapter as adapter

        try:
            # Cooperative contract: mark handoff before exposing the context;
            # every downstream owner must begin its own ticket before work.
            owner_context._handoff()
            result = adapter.call_lane_session(enabled=True, identity_bytes=identity_raw,
                expected_identity_sha256=identity_pin, descriptor_bytes=descriptor_raw,
                expected_descriptor_sha256=descriptor_pin, situation=situation, time=when, inputs=inputs,
                _owner_guard=guard, _owner_context=owner_context)
        except adapter.CallerCleanupUnknown:
            return _refuse_phase(3, "CALLER_ADAPTER_CLEANUP_UNKNOWN")
        # Unknown/unrecognized cleanup takes precedence over any status, including
        # a claimed answer or timeout. It never publishes validated stdout.
        if (type(result) is not dict or result.get("cleanup") != "chain_confirmed"
                or result.get("status") == "UNKNOWN"):
            inner = result.get("inner_status") if type(result) is dict else None
            phase = _INNER_PHASES.get(inner, "CALLER_RESULT_UNKNOWN") if type(inner) is str else "CALLER_RESULT_UNKNOWN"
            return _refuse_phase(3, phase)
        if result.get("status") == "TIMEOUT":
            return _refuse_phase(4, "CALLER_TIMEOUT")
        rendered = _output(result, lane, identity_pin, descriptor_pin, binding,
                           inputs["max_output_bytes"], token)
        if coverage_mode == "1":
            # No independent coverage pin exists: never imply completeness.
            # Wrap the validated empty result or metadata-only withheld result.
            wrapped = _canon({"schema": "wd.q4-lane-coverage-envelope.v1",
                              "coverage": "UNKNOWN", "freshness": "UNKNOWN",
                              "result": json.loads(rendered)}) + b"\n"
            _check(len(wrapped) <= inputs["max_output_bytes"]
                   and token.encode("utf-8", "strict") not in wrapped
                   and _canon(token)[1:-1] not in wrapped)
            rendered = wrapped.decode("ascii")
        if interrupted_marker is not None:
            wrapped = _canon({"schema": "wd.q4-lane-interrupted-envelope.v1",
                              "selection": "BASELINE_FALLBACK", "publication_state": "INTERRUPTED",
                              "freshness": "STALE", "result": json.loads(rendered)}) + b"\n"
            _check(len(wrapped) <= inputs["max_output_bytes"]
                   and token.encode("utf-8", "strict") not in wrapped
                   and _canon(token)[1:-1] not in wrapped)
            rendered = wrapped.decode("ascii")
        owner_context._finish()
        witness = guard._registry._issue()
        guard.release(witness)
        guard_released = True
        age_rechecked = _require_current_snapshot(descriptor, binding, inputs)
        _check(age_rechecked == age_snapshot)
        if interrupted_marker is not None:
            fresh = _configuration(_env("WD_Q4_SHARED_DESCRIPTOR_PATH"), descriptor_pin)
            _check(fresh == (descriptor, descriptor_raw, descriptor_pin, fleet_raw, binding, inputs))
            _check(_interrupted_baseline_marker(live, _env("WD_Q4_SHARED_DESCRIPTOR_PATH"), fresh)
                   == interrupted_marker)
        if live_publication is not None:
            live.recheck(live_publication["publication_path"], live_publication["publication_sha256"])
        # Last step before output: refuse a backward clock and any returned
        # card no longer CURRENT at the observed end (v6 is not rechecked).
        observed_end = _utc_now()
        lane_query.recheck_live_card_results(result["answer"]["results"], binding, when, observed_end)
        rendered = _with_content_cell_age(rendered, age_rechecked, observed_end,
                                          inputs["max_output_bytes"], token)
        _write(rendered)
        return 0
    except (KeyboardInterrupt, SystemExit):
        return _refuse_phase(130, "CALLER_INTERRUPTED")
    except TimeoutError:
        return _refuse_phase(4, "CALLER_TIMEOUT_UNKNOWN")
    except BaseException:
        # No exception detail, query, token, path or retry on any failure.
        return _refuse_phase(2, "CALLER_REFUSED")
    finally:
        # Do not abort an already released context: that would turn a successful
        # release into a spurious failure. The outer marker remains pending on
        # any unresolved path; this is not an atomic power-loss guarantee.
        if guard is not None and not guard_released:
            try:
                guard.mark_unknown()
            except BaseException:
                pass
        token = situation = identity_raw = inputs = result = None
        owner_context = rendered = None


if __name__ == "__main__":
    raise SystemExit(main())
