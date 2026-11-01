"""Default-off, caller-pinned session CLI adapter; no live discovery or launcher.

The caller authenticates the executable, this worker, its entire import
closure, fleet pin and separately supplied post-deployment descriptor pin BEFORE
starting Python. A matching hash is not authorization or caller authentication.
The descriptor is not stored in or pinned by the fleet, avoiding a manifest
hash cycle. Paths and hashes are configuration: referenced session data is not
validated by this adapter. The existing session CLI checks them before query.

Only the CLI reads the single bounded stdin question; neither this worker nor
the CLI creates a question file. Output is the CLI's bounded stream, not a
worker-validated receipt. Its caller MUST reject all stdout on nonzero exit,
and accept exit zero only with exactly one complete newline-framed JSON reply.
No whole-process watchdog, secure OS transport, production session identity,
source authentication or freshness is established here. Same-process patching
and same-user file races are outside this boundary.
KeyboardInterrupt during preflight is returned as status 130 without emitting
its arguments or a traceback. This does not sanitize other callers' frames.
SystemExit preflight payloads become one fixed diagnostic; ordinary nonzero
integer codes survive except the reserved cleanup/timeout/interruption codes.
"""

import hmac
import json
from pathlib import Path
import re
import sys

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_LANES = frozenset({"codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"})
_PATHS = frozenset({"bundle_root", "public_plan", "interpreter", "cwd", "binding_path", "fixed_plan_path",
                    "snapshot_path", "runtime_manifest_path"})
_HASHES = frozenset({"fleet_sha256", "interpreter_sha256", "binding_sha256", "fixed_plan_sha256",
                     "snapshot_sha256", "runtime_manifest_sha256"})
_LIMITS = {"interpreter_max_bytes": 128 * 1024 * 1024, "timeout_seconds": 1800, "max_output_bytes": 65536}
_KEYS = frozenset({"schema", "lanes"}) | _PATHS | _HASHES | frozenset(_LIMITS)
_ARGS = frozenset({"descriptor", "descriptor-sha256", "fleet", "fleet-sha256", "lane"})


def _pairs(pairs):
    out, seen = {}, set()
    for key, value in pairs:
        folded = key.casefold()
        if folded in seen:
            raise ValueError()
        seen.add(folded)
        out[key] = value
    return out


def _constant(_):
    raise ValueError()


def _options(argv):
    if type(argv) is not list or not argv or argv[0] != "--enabled":
        return None
    if len(argv) != 1 + 2 * len(_ARGS):
        raise ValueError()
    options = {}
    for index in range(1, len(argv), 2):
        key, value = argv[index:index + 2]
        if (type(key) is not str or not key.startswith("--") or key[2:] not in _ARGS
                or key[2:] in options or type(value) is not str or not value or len(value) > 32767):
            raise ValueError()
        options[key[2:]] = value
    if set(options) != _ARGS:
        raise ValueError()
    return options


def _cli_arguments(options, cli):
    if options["lane"] not in _LANES or any(_HEX.fullmatch(options[k]) is None
                                            for k in ("descriptor-sha256", "fleet-sha256")):
        raise ValueError()
    raw = cli._file(options["descriptor"], options["descriptor-sha256"], 65536)
    document = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_pairs, parse_constant=_constant)
    if type(document) is not dict or set(document) != _KEYS or document["schema"] != "wd.q4-shared-session-config.v1":
        raise ValueError()
    lanes = document["lanes"]
    if (type(lanes) is not list or len(lanes) != 5 or any(type(x) is not str for x in lanes)
            or frozenset(lanes) != _LANES):
        raise ValueError()
    for key in _HASHES:
        if type(document[key]) is not str or _HEX.fullmatch(document[key]) is None:
            raise ValueError()
    if not hmac.compare_digest(document["fleet_sha256"], options["fleet-sha256"]):
        raise ValueError()
    for key in _PATHS:
        value = document[key]
        if type(value) is not str or not value or len(value) > 32767:
            raise ValueError()
        path = Path(value)
        if not path.is_absolute() or ".." in path.parts or value.startswith(("\\\\", "//")):
            raise ValueError()
    for key, maximum in _LIMITS.items():
        if type(document[key]) is not int or not 1 <= document[key] <= maximum:
            raise ValueError()
    # Prove the separately pinned fleet bytes are readable before stdin. The
    # CLI repeats this check, and the session layer derives the exact roster.
    cli._file(options["fleet"], options["fleet-sha256"], cli._FILES["fleet"])
    flags = {"lane": options["lane"], "fleet": options["fleet"], "fleet-sha256": options["fleet-sha256"]}
    for key in ("bundle_root", "public_plan", "interpreter", "interpreter_sha256", "interpreter_max_bytes", "cwd",
                "timeout_seconds", "max_output_bytes"):
        flags[key.replace("_", "-")] = str(document[key])
    for key in ("binding", "fixed_plan", "snapshot", "runtime_manifest"):
        flags[key.replace("_", "-")] = document[key + "_path"]
        flags[(key + "_sha256").replace("_", "-")] = document[key + "_sha256"]
    if frozenset(flags) != cli._OPTIONS:
        raise ValueError()
    result = ["--enabled"]
    for key in sorted(flags):
        result.extend(("--" + key, flags[key]))
    return result


def main(argv=None):
    try:
        options = _options(sys.argv[1:] if argv is None else argv)
        if options is None:
            return 2
        from tools import wd_q4_lane_session_cli as cli
        arguments = _cli_arguments(options, cli)
    except KeyboardInterrupt:
        return 130
    except SystemExit as exc:
        code = exc.code if type(exc.code) is int and 1 <= exc.code <= 255 and exc.code not in (3, 4, 130) else 2
        try:
            sys.stderr.write('{"error":"session_exit","authority":"none"}\n')
        except Exception:
            pass
        return code
    except Exception:
        return 2
    # CLI catches and labels timeout/cleanup uncertainty; don't reclassify it.
    del options
    return cli.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
