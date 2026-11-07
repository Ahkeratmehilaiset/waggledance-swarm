"""Default-off, bounded CLI candidate for the cooperative Q4 caller adapter.

No arguments emits OFF without reading environment, stdin, configuration or the
adapter. --enabled reads one JSON situation/time query exclusively from stdin.
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

Before entering a query and again before publishing its result, the public plan
bound by the pinned lane binding identifies an index root whose q4-latest.json
must still match the pointer pinned by this descriptor. A changed/missing pointer
refuses output. This is an equality check at two instants, not authenticated time,
source freshness, anti-ABA protection, or a default descriptor refresh.

An explicitly selected WD_Q4_LIVE_MODE=1 opts into a separate, mutable local
selector under the baseline descriptor's generation root. Each query rechecks
the candidate against the baseline bundle, fleet, runtime and policy bindings,
and refuses if the selector or index pointer changes before output. It does not
authenticate the selector's same-user publisher or lane and does not prove
wall-clock or source freshness. Missing or malformed live selectors refuse.

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
from datetime import datetime
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
        raw = stream.read(limit + 1)
    _check(type(raw) is bytes and 0 < len(raw) <= limit)
    _same_digest(raw, pin)
    return raw


def _integer(value, ceiling):
    _check(type(value) is int and 0 < value <= ceiling)
    return value


def _binding(value):
    _check(type(value) is dict and frozenset(value) in (_BINDING_KEYS, _CARD_BINDING_KEYS, _STAGED_BINDING_KEYS))
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
    """Refuse unless this exact pinned public plan still names the current index."""
    from tools import wd_q4_lane_link as link
    binding_doc = _json(inputs["binding_bytes"])
    _check(binding_doc.get("schema") == "wd.q4-lane-binding.v1"
           and binding_doc.get("binding") == binding)
    public_hash = _hex(binding_doc.get("public_plan_sha256"))
    _, public_bytes, public_pins = link._public_plan(descriptor["public_plan"], public_hash)
    _check(("card_decision_sha256" in public_pins) == ("card_decision_sha256" in binding)
           and all(public_pins[key] == binding[key] for key in public_pins))
    public = _json(public_bytes)
    staged = "card_policy_sha256" in binding
    _check((public.get("schema") == "wd.q4-public-wrapper-plan.v3") == staged)
    if staged:
        _read(str(_path(public["pointer_path"])), 1048576, binding["pointer_sha256"])
    else:
        root = _path(public["root"])
        _read(str(root / "q4-latest.json"), 16384, binding["pointer_sha256"])


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
            live_publication = live.select(**selection)
            descriptor, descriptor_raw, descriptor_pin, fleet_raw, binding, inputs = live_publication["configuration"]
        _require_current_snapshot(descriptor, binding, inputs)
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
            situation, when = _query()
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
            # Keep the validated v1 result and its answer digest unchanged.
            wrapped = _canon({"schema": "wd.q4-lane-coverage-envelope.v1",
                              "coverage": "UNKNOWN", "freshness": "UNKNOWN",
                              "result": json.loads(rendered)}) + b"\n"
            _check(len(wrapped) <= inputs["max_output_bytes"]
                   and token.encode("utf-8", "strict") not in wrapped
                   and _canon(token)[1:-1] not in wrapped)
            rendered = wrapped.decode("ascii")
        owner_context._finish()
        witness = guard._registry._issue()
        guard.release(witness)
        guard_released = True
        _require_current_snapshot(descriptor, binding, inputs)
        if live_publication is not None:
            live.recheck(live_publication["publication_path"], live_publication["publication_sha256"])
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
