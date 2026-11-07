"""Explicit, default-off, one-shot continuation of a private genesis tuple.

The caller supplies an independently authorized config hash, manifest anchor,
source-prefix identity and checkpoint hash. Hashes do not authenticate their
source. Output contains only closed status values and result hashes; there is
no scheduling, retry, installation, activation or recovery of unknown work.
"""
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

from tools import wd_q4_production_continuation as continuation
from tools.wd_q4_descriptor_cli import _read_pinned
from tools.wd_q4_genesis_cli import _c_path, _doc

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_KEYS = frozenset({"schema", "bundle_root", "manifest_sha256", "tuple_path", "tuple_sha256",
                   "policy_path", "policy_sha256", "public_policy_path", "public_policy_sha256",
                   "fleet_sha256", "pointer_sha256", "source_identity", "source_prefix_bytes",
                   "source_prefix_sha256"})
_STATUSES = frozenset({"partial_ready_prefix", "caught_up_to_cutoff", "DEFERRED"})


def _prepare(path, digest):
    config = _doc(_read_pinned(_c_path(path), 65536, digest, "config_refused"))
    legacy = config.get("schema") == "wd.q4-continuation-cli.v1" and set(config) == _KEYS
    watermarked = (config.get("schema") == "wd.q4-continuation-cli.v3"
                   and set(config) == _KEYS | {"cutoff_utc", "previous_cutoff_utc", "watermark_previous_record_sha256"})
    explicit = watermarked or (config.get("schema") == "wd.q4-continuation-cli.v2"
                and set(config) == _KEYS | {"cutoff_utc", "previous_cutoff_utc"})
    if not (legacy or explicit):
        raise ValueError()
    if watermarked and config["watermark_previous_record_sha256"] is not None:
        prior = config["watermark_previous_record_sha256"]
        if type(prior) is not str or _HEX.fullmatch(prior) is None:
            raise ValueError()
    if explicit and (continuation.runner._cutoff_datetime(config["cutoff_utc"])
                     < continuation.runner._cutoff_datetime(config["previous_cutoff_utc"])):
        raise ValueError()
    for key in ("manifest_sha256", "tuple_sha256", "policy_sha256", "public_policy_sha256",
                "fleet_sha256", "pointer_sha256", "source_prefix_sha256"):
        if type(config[key]) is not str or _HEX.fullmatch(config[key]) is None:
            raise ValueError()
    if (type(config["source_identity"]) is not str or not config["source_identity"]
            or type(config["source_prefix_bytes"]) is not int or config["source_prefix_bytes"] < 1):
        raise ValueError()
    root = Path(_c_path(config["bundle_root"]))
    anchor = os.environ.get("WD_REBOOT_EXPECTED_MANIFEST_HASH", "").lower()
    if not hmac.compare_digest(anchor, config["manifest_sha256"]):
        raise ValueError()
    manifest = _doc(_read_pinned(str(root / "deployment-manifest.json"), 2 * 1024 * 1024,
                                 config["manifest_sha256"], "manifest_refused"))
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise ValueError()
    files = manifest.get("files")
    if type(files) is not dict or str(files.get("wd-fleet.json", "")).lower() != config["fleet_sha256"]:
        raise ValueError()
    # Reject a similarly named script or imported continuation from another tree.
    for relative, actual in (("tools/wd_q4_production_continuation_cli.py", __file__),
                             ("tools/wd_q4_production_continuation.py", continuation.__file__)):
        code = root / "tools-bootstrap" / relative
        pin = str(files.get("tools-bootstrap/" + relative, "")).lower()
        if not _HEX.fullmatch(pin) or not os.path.samefile(code, actual):
            raise ValueError()
        _read_pinned(str(code), 1024 * 1024, pin, "code_refused")
    if watermarked:
        name = "tools.wd_q4_cutoff_watermark"
        loaded = sys.modules.get(name)
        spec = importlib.util.find_spec(name)
        actual = getattr(loaded, "__file__", None) if loaded is not None else getattr(spec, "origin", None)
        code = root / "tools-bootstrap/tools/wd_q4_cutoff_watermark.py"
        pin = str(files.get("tools-bootstrap/tools/wd_q4_cutoff_watermark.py", "")).lower()
        if not _HEX.fullmatch(pin) or not actual or not os.path.samefile(code, actual):
            raise ValueError()
        _read_pinned(str(code), 1024 * 1024, pin, "code_refused")
    tuple_path = _c_path(config["tuple_path"])
    tuple_bytes = _read_pinned(tuple_path, 65536, config["tuple_sha256"], "tuple_refused")
    document = _doc(tuple_bytes)
    expected = {"identity": config["source_identity"], "prefix_bytes": config["source_prefix_bytes"],
                "prefix_sha256": config["source_prefix_sha256"]}
    if document.get("independent_source_pin") != expected:
        raise ValueError()
    # Check the hash-bound route before publishing: a private tuple cannot
    # reside in the checkpoint tree. Resolve Windows final names so 8.3 aliases
    # do not defeat containment. The continuation repeats its full validation.
    route = _doc(continuation._part(document["route"], 65536))
    checkpoint = continuation.genesis.roots._root(_c_path(route["checkpoint_root"]))
    continuation.genesis.roots._observe(checkpoint)
    tuple_final = os.path.normcase(os.path.realpath(tuple_path, strict=True))
    checkpoint_final = os.path.normcase(os.path.realpath(str(checkpoint), strict=True))
    if os.path.commonpath((tuple_final, checkpoint_final)) == checkpoint_final:
        raise ValueError()
    policy = _read_pinned(_c_path(config["policy_path"]), 65536, config["policy_sha256"], "policy_refused")
    public = _read_pinned(_c_path(config["public_policy_path"]), 65536,
                          config["public_policy_sha256"], "policy_refused")
    fleet = _read_pinned(str(root / "wd-fleet.json"), 1024 * 1024, config["fleet_sha256"], "fleet_refused")
    result = {"enabled": True, "tuple_bytes": tuple_bytes, "expected_tuple_sha256": config["tuple_sha256"],
            "expected_pointer_sha256": config["pointer_sha256"], "policy_bytes": policy,
            "public_policy_bytes": public, "fleet_bytes": fleet, "expected_fleet_sha256": config["fleet_sha256"]}
    if explicit:
        result.update(cutoff_utc=config["cutoff_utc"], previous_cutoff_utc=config["previous_cutoff_utc"])
    if watermarked:
        result.update(watermark_enabled=True, expected_watermark_record_sha256=config["watermark_previous_record_sha256"])
    return result


def _result(value, inputs):
    keys = {"status", "runner_calls", "pointer_sha256", "runner_receipt_sha256", "coverage",
            "source_authenticity", "freshness"}
    explicit = "cutoff_utc" in inputs or "previous_cutoff_utc" in inputs
    added = {"cutoff_utc", "previous_cutoff_utc", "route_sha256", "cutoff_monotonicity"}
    watermarked = inputs.get("watermark_enabled") is True
    if watermarked:
        added.add("watermark_record_sha256")
    if (type(value) is not dict or set(value) != (keys | added if explicit else keys) or value["status"] not in _STATUSES
            or type(value["runner_calls"]) is not int or value["runner_calls"] != 1
            or value["source_authenticity"] != "UNKNOWN" or value["freshness"] != "UNKNOWN"
            or type(value["coverage"]) is not str
            or any(type(value[key]) is not str or _HEX.fullmatch(value[key]) is None
                   for key in ("pointer_sha256", "runner_receipt_sha256"))):
        raise ValueError()
    output = {key: value[key] for key in ("status", "pointer_sha256", "runner_receipt_sha256")}
    if explicit:
        if (value["cutoff_utc"] != inputs.get("cutoff_utc")
                or value["previous_cutoff_utc"] != inputs.get("previous_cutoff_utc")
                or value["cutoff_monotonicity"] != ("COOPERATIVE_LEASE_PERSISTED_CRASH_UNKNOWN" if watermarked
                                                     else "CALLER_ASSERTED_REPLAY_CRASH_UNPROVEN")
                or (watermarked and (type(value["watermark_record_sha256"]) is not str
                    or _HEX.fullmatch(value["watermark_record_sha256"]) is None))
                or type(value["route_sha256"]) is not str or _HEX.fullmatch(value["route_sha256"]) is None):
            raise ValueError()
        output.update({key: value[key] for key in added})
    return output


def _emit(value):
    print(json.dumps(value, separators=(",", ":")))


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        _emit({"status": "OFF"})
        return 0
    if (len(args) != 5 or args[0] != "--enable" or args[1] != "--config"
            or args[3] != "--sha256" or _HEX.fullmatch(args[4]) is None):
        _emit({"status": "REFUSED"})
        return 2
    try:
        inputs = _prepare(args[2], args[4])
    except Exception:
        _emit({"status": "REFUSED"})
        return 2
    except BaseException:
        _emit({"status": "UNKNOWN"})
        return 3
    try:
        result = _result(continuation.continue_once(**inputs), inputs)
    except continuation.ContinuationRefused:
        _emit({"status": "REFUSED"})
        return 2
    except BaseException:
        _emit({"status": "UNKNOWN"})
        return 3
    _emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
