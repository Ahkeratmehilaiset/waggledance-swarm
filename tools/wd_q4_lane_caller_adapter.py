"""Default-off per-lane caller for the existing bound Q4 session chain: declared-consistent, never authenticated.

One call of ``tools.wd_q4_lane_session_client.query_session`` (the Python layer of the existing
bound-session chain) for the lane named by a caller-independent identity document, after the
identity, the installed shared descriptor and the sixteen pinned chain inputs have been checked
for mutual consistency. The lane is never an argument of this function: it comes from the identity
document, which must name the current process and must agree with the deployment-pinned roster.

What this module proves and what it does not:

* ``lane_binding = "DECLARED_CONSISTENT"`` means only that the identity document hashes to the
  separately supplied pin, names this process id, names a lane that is in the pinned roster with
  the roster's uuid, places its cwd under its declared worktree (string prefix on path parts, no
  filesystem resolution: junctions and reparse points are NOT resolved here), carries a
  64-hex owner-token digest (presence and shape only: no independently published digest exists
  today to compare it with), and pins the same descriptor whose pins equal the chain inputs.
* ``lane_authentication`` is always ``"UNKNOWN"``. All lanes run as one Windows user; a malicious
  same-user process can present a consistent identity document. Nothing here protects against it.
* The answer is the existing chain's validated document, re-checked here for the declared lane,
  the descriptor's expected binding and a closed shape; any other output is discarded as REFUSED.
* ``cleanup = "chain_confirmed"`` repeats the existing chain's own confirmation (the fixed transport
  drains its Job within its cleanup budget or raises a cleanup-unknown error). There is no outer
  whole-call watchdog here; an UNKNOWN cleanup quarantines this process only (no persistent marker).
* Default off reads no argument. Enabled, it reads no file, no environment and no clock, starts no
  process by itself and writes nothing; the chain below starts the pinned fixed peer.
* No query text, token, path or exception detail is placed in the result or in any exception.
  Traceback frame locals of the chain are outside this boundary.
"""

import hashlib
import hmac
import json
import os
import re
import secrets

from tools import wd_q4_lane_session_client as session
from tools import wd_q4_roster_from_fleet as roster
from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown
from tools.wd_q4_secure_transport import TransportCleanupUnknown

SCHEMA = "wd.q4-lane-caller-result.v1"
IDENTITY_SCHEMA = "wd.q4-lane-caller-identity.v1"
DESCRIPTOR_SCHEMA = "wd.q4-shared-session-config.v2"
MAX_DOCUMENT_BYTES = 65536
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_IDENTITY_KEYS = frozenset({"schema", "agent", "agent_uuid", "worktree", "cwd", "owner_token_sha256", "caller_pid",
                            "run_id", "descriptor_sha256"})
_INPUT_KEYS = frozenset({"fleet_bytes", "expected_fleet_sha256", "binding_bytes", "expected_binding_sha256",
                         "bundle_root", "public_plan_path", "executable", "expected_interpreter_sha256",
                         "interpreter_max_bytes", "fixed_plan_bytes", "expected_fixed_plan_sha256",
                         "fixed_bundle_bytes", "runtime_manifest_bytes", "cwd", "timeout_seconds",
                         "max_output_bytes"})
_DESCRIPTOR_KEYS = frozenset({"schema", "fleet_sha256", "lanes", "bundle_root", "public_plan", "interpreter", "cwd",
                              "binding_path", "fixed_plan_path", "snapshot_path", "runtime_manifest_path",
                              "interpreter_sha256", "binding_sha256", "fixed_plan_sha256", "snapshot_sha256",
                              "runtime_manifest_sha256", "interpreter_max_bytes", "timeout_seconds",
                              "max_output_bytes", "outer", "expected_binding"})
_BINDING_KEYS = frozenset({"pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
                           "roster_sha256", "public_policy_sha256", "descriptor_sha256", "projection_sha256"})
_ANSWER_KEYS = frozenset({"schema", "lane", "status", "reason", "result_count", "results", "binding", "trust"})
_VALIDATED = frozenset({"ANSWER", "ABSTAIN", "DEGRADED"})
_RESULT_KEYS = ("schema", "status", "lane", "lane_binding", "lane_authentication", "cleanup", "inner_status",
                "result_count", "answer", "answer_sha256", "identity_sha256", "descriptor_sha256", "call_id")

_QUARANTINED = False  # An unconfirmed cleanup or an interrupted call: never start another chain call here.
_HOLD = []  # Attestation leases retained after an unconfirmed close, for explicit handling only.


class CallerRefused(ValueError):
    """Fixed refusal: ``caller_refused`` or ``caller_quarantined``; no input, path or detail inside."""


def _refuse():
    raise ValueError()


def _unique(pairs):
    document = {}
    for key, value in pairs:
        if type(key) is not str or key in document:
            _refuse()
        document[key] = value
    return document


def _constant(_value):
    _refuse()


def _parse(raw, expected_sha256, keys, schema):
    if (type(raw) is not bytes or not 0 < len(raw) <= MAX_DOCUMENT_BYTES or type(expected_sha256) is not str
            or _HEX64.fullmatch(expected_sha256) is None
            or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)):
        _refuse()
    document = json.loads(raw.decode("ascii", "strict"), object_pairs_hook=_unique, parse_constant=_constant)
    if type(document) is not dict or frozenset(document) != keys or document["schema"] != schema:
        _refuse()
    return document


def _parts(text):
    if type(text) is not str or not text or any(ord(c) < 32 or ord(c) == 127 for c in text):
        _refuse()
    return tuple(part.casefold() for part in text.replace("/", "\\").rstrip("\\").split("\\") if part != "")


def _consistent_identity(identity, descriptor_sha256, fleet_bytes, expected_fleet_sha256):
    """Declared consistency of the identity document with this process, the roster and the descriptor pin."""
    if (identity["agent_uuid"] is None or type(identity["agent_uuid"]) is not str
            or _UUID.fullmatch(identity["agent_uuid"]) is None
            or type(identity["caller_pid"]) is not int or identity["caller_pid"] != os.getpid()
            or type(identity["owner_token_sha256"]) is not str or _HEX64.fullmatch(identity["owner_token_sha256"]) is None
            or type(identity["run_id"]) is not str or not identity["run_id"]
            or type(identity["descriptor_sha256"]) is not str
            or not hmac.compare_digest(identity["descriptor_sha256"], descriptor_sha256)
            or type(identity["agent"]) is not str):
        _refuse()
    worktree, cwd = _parts(identity["worktree"]), _parts(identity["cwd"])
    if not worktree or cwd[:len(worktree)] != worktree:
        _refuse()
    snapshot, _digest = roster.build_roster_snapshot(fleet_bytes, expected_fleet_sha256, enabled=True)
    members = json.loads(snapshot.decode("ascii"))
    entries = [*members["lanes"], members["tools_supervisor"]]
    expected = [entry["agent_uuid"] for entry in entries if entry["agent"] == identity["agent"]]
    if len(expected) != 1 or not hmac.compare_digest(expected[0], identity["agent_uuid"]):
        _refuse()
    return identity["agent"]


def _consistent_descriptor(descriptor, agent, inputs):
    """The installed descriptor pins and paths must equal the chain inputs the caller supplies."""
    lanes = descriptor["lanes"]
    if type(lanes) is not list or not all(type(lane) is str for lane in lanes) or agent not in lanes:
        _refuse()
    binding = descriptor["expected_binding"]
    if (type(binding) is not dict or frozenset(binding) != _BINDING_KEYS
            or not all(type(value) is str and _HEX64.fullmatch(value) for value in binding.values())
            or type(descriptor["outer"]) is not dict):
        _refuse()
    pins = (("fleet_sha256", inputs["expected_fleet_sha256"]),
            ("binding_sha256", inputs["expected_binding_sha256"]),
            ("fixed_plan_sha256", inputs["expected_fixed_plan_sha256"]),
            ("interpreter_sha256", inputs["expected_interpreter_sha256"]),
            ("snapshot_sha256", hashlib.sha256(inputs["fixed_bundle_bytes"]).hexdigest()),
            ("runtime_manifest_sha256", hashlib.sha256(inputs["runtime_manifest_bytes"]).hexdigest()))
    for key, expected in pins:
        value = descriptor[key]
        if (type(value) is not str or type(expected) is not str or _HEX64.fullmatch(value) is None
                or not hmac.compare_digest(value, expected)):
            _refuse()
    for key, expected in (("bundle_root", inputs["bundle_root"]), ("public_plan", inputs["public_plan_path"]),
                          ("interpreter", inputs["executable"]), ("cwd", inputs["cwd"])):
        if type(descriptor[key]) is not str or type(expected) is not str or descriptor[key] != expected:
            _refuse()
    for key in ("interpreter_max_bytes", "timeout_seconds", "max_output_bytes"):
        value, expected = descriptor[key], inputs[key]
        if type(value) is not int or type(expected) is not int or value != expected:
            _refuse()
    return {key: binding[key] for key in sorted(_BINDING_KEYS)}


def _validated(answer, agent, expected_binding):
    """``(inner_status, answer, result_count)`` for a closed chain answer of the declared lane; else ``None``."""
    if (type(answer) is not dict or frozenset(answer) != _ANSWER_KEYS or type(answer["status"]) is not str
            or type(answer["lane"]) is not str or answer["lane"] != agent or type(answer["trust"]) is not dict
            or answer["trust"].get("lane_authentication") != "UNKNOWN" or type(answer["results"]) is not list
            or type(answer["result_count"]) is not int or answer["result_count"] != len(answer["results"])):
        return None
    if answer["status"] == "REFUSED":
        if answer["results"] or answer["binding"] is not None:
            return None
        return "REFUSED", None, 0
    binding = answer["binding"]
    if (answer["status"] not in _VALIDATED or type(binding) is not dict or frozenset(binding) != _BINDING_KEYS
            or any(type(binding[key]) is not str or not hmac.compare_digest(binding[key], expected_binding[key])
                   for key in _BINDING_KEYS)):
        return None
    return answer["status"], answer, answer["result_count"]


def call_lane_session(*, enabled=False, identity_bytes=None, expected_identity_sha256=None, descriptor_bytes=None,
                      expected_descriptor_sha256=None, situation=None, time=None, inputs=None):
    """One declared-consistent call of the existing chain; ``{"status": "OFF"}`` when ``enabled`` is ``False``."""
    global _QUARANTINED
    if enabled is False:
        return {"status": "OFF"}
    if _QUARANTINED:
        raise CallerRefused("caller_quarantined")
    agent = binding = identity = descriptor = chain_inputs = None
    try:
        if (enabled is not True or type(inputs) is not dict or frozenset(inputs) != _INPUT_KEYS
                or type(inputs["fixed_bundle_bytes"]) is not bytes or type(inputs["runtime_manifest_bytes"]) is not bytes):
            _refuse()
        identity = _parse(identity_bytes, expected_identity_sha256, _IDENTITY_KEYS, IDENTITY_SCHEMA)
        descriptor = _parse(descriptor_bytes, expected_descriptor_sha256, _DESCRIPTOR_KEYS, DESCRIPTOR_SCHEMA)
        agent = _consistent_identity(identity, expected_descriptor_sha256, inputs["fleet_bytes"],
                                     inputs["expected_fleet_sha256"])
        binding = _consistent_descriptor(descriptor, agent, inputs)
        chain_inputs = dict(inputs)
    except Exception:
        agent = None
    finally:
        identity = descriptor = None
    if agent is None:
        del identity_bytes, descriptor_bytes, situation, time, inputs, chain_inputs
        raise CallerRefused("caller_refused")
    result = {"schema": SCHEMA, "status": "REFUSED", "lane": agent, "lane_binding": "DECLARED_CONSISTENT",
              "lane_authentication": "UNKNOWN", "cleanup": "chain_confirmed", "inner_status": "session_refused",
              "result_count": 0, "answer": None, "answer_sha256": None, "identity_sha256": expected_identity_sha256,
              "descriptor_sha256": expected_descriptor_sha256, "call_id": secrets.token_hex(16)}
    signal = None
    try:
        answer = session.query_session(lane=agent, situation=situation, time=time, **chain_inputs, enabled=True)
        validated = _validated(answer, agent, binding)
        if validated is None:
            result["inner_status"] = "output_refused"
        else:
            result["inner_status"], result["answer"], result["result_count"] = validated
            if result["answer"] is not None:
                result["status"] = "VALIDATED"
                canonical = json.dumps(result["answer"], sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                       allow_nan=False).encode("ascii")
                result["answer_sha256"] = hashlib.sha256(canonical).hexdigest()
    except AttestationCleanupUnknown as exc:
        _QUARANTINED = True
        _HOLD.append(exc.lease)
        result["status"], result["cleanup"], result["inner_status"] = "UNKNOWN", "unknown", "attestation_cleanup_unknown"
    except TransportCleanupUnknown:
        _QUARANTINED = True
        result["status"], result["cleanup"], result["inner_status"] = "UNKNOWN", "unknown", "transport_cleanup_unknown"
    except TimeoutError:
        result["status"], result["inner_status"] = "TIMEOUT", "timeout"
    except KeyboardInterrupt:
        _QUARANTINED = True
        signal = KeyboardInterrupt()
    except SystemExit as exc:
        _QUARANTINED = True
        signal = SystemExit(exc.code if exc.code is None or type(exc.code) is int else 1)
    except Exception:
        result["inner_status"] = "session_refused"
    finally:
        answer = validated = canonical = None
        del situation, time, inputs, chain_inputs, identity_bytes, descriptor_bytes
    if signal is not None:
        raise signal
    return {key: result[key] for key in _RESULT_KEYS}
