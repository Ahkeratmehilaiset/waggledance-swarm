"""Q4 stdin boundary, intentionally inactive until an external trust chain exists.

Environment hashes alone are not an external anchor. No production resolver or
launcher binding is implemented here: enabling an environment flag cannot make
this entrypoint execute a model, access memory, or establish agent identity.
Deployment must independently pin interpreter, launcher, bootstrap, verifier,
runtime manifest, generation, model revision/assets, descriptor and lock. This
module does not establish OS-level stdin/log privacy, complete containment,
same-user race resistance, model asset provenance, or a whole-call deadline.
"""

from dataclasses import dataclass
import json
import math
import ntpath
import os
import re
import sys

MAX_QUERY = 65536
MAX_SITUATION = 4000
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_REV = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SCOPE = "wd.q4-shared-projected-events.v1"


class QueryRefused(ValueError):
    """A fixed refusal with no input or path content."""


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise QueryRefused("invalid_query")
        result[key] = value
    return result


def read_query(stream):
    """Read one bounded object; callers must first establish an external anchor."""
    try:
        raw = stream.read(MAX_QUERY + 1)
        if not isinstance(raw, bytes) or len(raw) > MAX_QUERY:
            raise QueryRefused("invalid_query")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(QueryRefused("invalid_query")))
        if not isinstance(value, dict) or set(value) != {"situation", "time"}:
            raise QueryRefused("invalid_query")
        situation, when = value["situation"], value["time"]
        if (not isinstance(situation, str) or not situation.strip() or len(situation) > MAX_SITUATION
                or not isinstance(when, str) or not when or len(when) > 128):
            raise QueryRefused("invalid_query")
        return raw
    except (UnicodeError, ValueError, TypeError, RecursionError, OSError):
        raise QueryRefused("invalid_query") from None


def _verified_channel():
    # Deliberate refusal: a future reviewed implementation must establish the
    # independently anchored complete tuple, including model/runtime pins.
    return None


@dataclass(frozen=True)
class VerifiedQ4Config:
    """Caller-asserted binding; this type itself authenticates nothing."""
    interpreter: str
    interpreter_sha256: str
    bootstrap: str
    bootstrap_sha256: str
    verifier_sha256: str
    runtime_root: str
    runtime_manifest: str
    runtime_manifest_sha256: str
    runtime_generation: str
    runtime_revision: str
    memory: str
    descriptor: str
    descriptor_sha256: str
    lock_file: str
    lock_timeout: str
    timeout: float


def _invoke_launcher(**kwargs):
    from sandbox.mind.step2.runtime_launcher import launch
    return launch(**kwargs)


def _validate_config(config):
    if type(config) is not VerifiedQ4Config:
        raise QueryRefused("invalid_binding")
    for value in (config.interpreter, config.bootstrap, config.runtime_root, config.runtime_manifest,
                  config.memory, config.descriptor, config.lock_file):
        if (not isinstance(value, str) or not re.match(r"^[A-Za-z]:\\", value)
                or ":" in value[2:] or "\x00" in value or ntpath.normpath(value) != value):
            raise QueryRefused("invalid_binding")
    for value in (config.interpreter_sha256, config.bootstrap_sha256, config.verifier_sha256,
                  config.runtime_manifest_sha256, config.descriptor_sha256):
        if not isinstance(value, str) or not _HEX.fullmatch(value):
            raise QueryRefused("invalid_binding")
    if any(not isinstance(value, str) or not _REV.fullmatch(value)
           for value in (config.runtime_generation, config.runtime_revision)):
        raise QueryRefused("invalid_binding")
    try:
        if not isinstance(config.lock_timeout, str):
            raise ValueError()
        lock_timeout = float(config.lock_timeout)
        if not math.isfinite(lock_timeout) or not 0 <= lock_timeout <= 30:
            raise ValueError()
        if type(config.timeout) not in (int, float) or not math.isfinite(config.timeout) or not 0 < config.timeout <= 1800:
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise QueryRefused("invalid_binding") from None


def _project_result(result, descriptor_sha256):
    if not isinstance(result, dict) or result.get("status") not in ("ANSWER", "ABSTAIN", "DEGRADED", "REFUSED"):
        raise QueryRefused("invalid_result")
    status = result["status"]
    if status == "REFUSED":
        if result.get("results") not in (None, []):
            raise QueryRefused("invalid_result")
        return {"status": "REFUSED", "reason": "runtime_refused", "results": []}
    if (result.get("scope") != _SCOPE or result.get("descriptor_sha256") != descriptor_sha256
            or not isinstance(result.get("memory_sha256"), str) or not _HEX.fullmatch(result["memory_sha256"])):
        raise QueryRefused("invalid_result")
    items = result.get("results")
    if not isinstance(items, list) or len(items) > 100:
        raise QueryRefused("invalid_result")
    projected = []
    for item in items:
        if (not isinstance(item, dict) or item.get("ref_kind") != "sha256_of_projected_metadata"
                or item.get("canonical_event") is not False):
            raise QueryRefused("invalid_result")
        hashes = {}
        for name in ("ref_sha256", "doc_id_sha256"):
            if not isinstance(item.get(name), str) or not _HEX.fullmatch(item[name]):
                raise QueryRefused("invalid_result")
            hashes[name] = item[name]
        projected.append(hashes)
    return {"status": status, "scope": _SCOPE, "descriptor_sha256": descriptor_sha256,
            "memory_sha256": result["memory_sha256"], "results": projected}


def query_with_verified_config(config, stdin):
    """Use a caller-anchored tuple; caller must independently establish trust.

    Only syntax is checked here. Caller must establish identity, hashes and
    containment; the launcher checks selected pins and the bootstrap checks the
    runtime manifest. Hashes are data channels, not complete privacy protection.
    """
    _validate_config(config)
    query = read_query(stdin)
    args = {"runtime-root": config.runtime_root, "runtime-manifest": config.runtime_manifest,
            "runtime-manifest-sha256": config.runtime_manifest_sha256,
            "runtime-generation": config.runtime_generation, "runtime-revision": config.runtime_revision,
            "memory": config.memory, "descriptor": config.descriptor,
            "descriptor-sha256": config.descriptor_sha256, "lock-file": config.lock_file,
            "lock-timeout": config.lock_timeout}
    try:
        result = _invoke_launcher(interpreter=config.interpreter, interpreter_sha256=config.interpreter_sha256,
                                  bootstrap=config.bootstrap, bootstrap_sha256=config.bootstrap_sha256,
                                  verifier_sha256=config.verifier_sha256, tuple_args=args, query=query,
                                  timeout=config.timeout)
    except Exception:
        raise QueryRefused("launcher_refused") from None
    return _project_result(result, config.descriptor_sha256)


def _refuse(out, reason):
    out.write(json.dumps({"schema": "wd.q4-agent-query.v1", "status": "REFUSED", "reason": reason}) + "\n")
    return 2


def run(argv, stdin, stdout, environ):
    if argv:
        return _refuse(stdout, "arguments_not_allowed")
    if environ.get("WD_Q4_READ_ENABLED") != "1":
        return _refuse(stdout, "disabled")
    channel = _verified_channel()
    if channel is None:
        return _refuse(stdout, "external_anchor_unavailable")
    # CLI serialization and the wrapper's trust channel are not implemented.
    return _refuse(stdout, "unavailable")


def main():
    return run(sys.argv[1:], sys.stdin.buffer, sys.stdout, os.environ)


if __name__ == "__main__":
    raise SystemExit(main())
