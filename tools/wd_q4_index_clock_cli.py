"""Default-off, one-shot CLI around ``tools.wd_q4_index_clock_runner``; not a scheduler or service.

Usage: ``python -m tools.wd_q4_index_clock_cli --enabled --config PATH --config-sha256 HEX --cutoff-utc
YYYY-MM-DDTHH:MM:SSZ``. Without ``--enabled`` it prints ``{"status": "OFF"}`` and reads nothing.

The config (schema ``wd.q4-index-clock-config.v1``, closed keys, at most 64 KiB) names the frozen plan, the
anchor, policy, roster and public-policy files each with its sha256, the source and generation paths, the
source cursor, the start prefix and the three roots. Every pinned file is read once with a size cap and
compared with its pin; the typed child inputs are rebuilt from those bytes and the runner is called exactly
once. No clock is read: the cutoff is the caller's explicit UTC second and stays an unverified assertion
(the output says ``CALLER_UNVERIFIED``). Nothing is discovered, no latest pointer is chosen, nothing is
retried or cleaned up.

Exit 0 prints one JSON line with a closed key set. Exit 2 (``index_cli_refused``) means the child was not
entered. Exit 3 (``index_outcome_unknown``) means the child may have moved the pointer or left a fence: the
caller must quarantine and reconcile the roots and must not retry automatically. This includes a failed,
short or unflushed stdout receipt after the run and any interrupt once the runner call has started, since
the runner may already have published. Exit 130 (``index_cli_interrupted``) is an interrupt before the
runner call, including one while writing the OFF line. A SystemExit raised before the runner call, or any
other failed OFF write, maps to exit 2 and its payload is discarded. Stderr carries only those fixed tokens;
no path, pin, query or private value is echoed, and the schema has no query field. Path checks are syntactic
plus a regular-file check at read time; they do not defeat a same-user replacement between verification and
use.
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

SCHEMA = "wd.q4-index-clock-config.v1"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_CUTOFF = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})Z\Z")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_CONFIG_LIMIT = 65536
_PIN_LIMITS = {"plan": 1 << 16, "anchor": 16384, "policy": 65536, "roster": 1 << 20, "public_policy": 65536}
_KEYS = frozenset({"schema", "plan", "anchor", "policy", "roster", "public_policy", "source_path",
                   "generation_path", "source_cursor", "max_bytes", "max_rows", "start_prefix", "root",
                   "private_state_root", "trusted_model_root"})
_CURSOR_KEYS = frozenset({"offset", "file_identity", "generation"})
_PREFIX_KEYS = _CURSOR_KEYS | {"sha256"}
_MAX_BYTES = 4 * 1024 * 1024
_MAX_ROWS = 10000
_FLAGS = ("--config", "--config-sha256", "--cutoff-utc")
_CHILD_KEYS = ("status", "pointer_sha256", "generation", "coverage", "freshness", "source_authenticity",
               "publisher_authentication", "source_refusals", "plan_sha256", "encoder_identity_source")


class CliRefused(ValueError):
    """Refused before the runner was entered."""


def _strict_object(pairs):
    seen = set()
    for key, _ in pairs:
        folded = "".join(c.upper() if len(c.upper()) == 1 else c for c in key)
        if folded in seen:
            raise ValueError()
        seen.add(folded)
    return dict(pairs)


def _refuse_constant(_):
    raise ValueError()


def _absolute(value):
    return (type(value) is str and 0 < len(value) <= 4096 and "\x00" not in value and Path(value).is_absolute()
            and ".." not in Path(value).parts and not value.startswith(("\\\\", "//")))


def _read_pinned(path_text, digest, limit):
    """One bounded read of a regular, non-link file whose sha256 must equal ``digest``."""
    if not _absolute(path_text) or type(digest) is not str or not _HEX64.match(digest):
        raise CliRefused()
    info = os.lstat(path_text)
    if (not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE
            or not 0 < info.st_size <= limit):
        raise CliRefused()
    with open(path_text, "rb") as stream:
        data = stream.read(limit + 1)
    if not 0 < len(data) <= limit or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), digest):
        raise CliRefused()
    return data


def _count(value, low, high):
    return type(value) is int and low <= value <= high


def _cursor(value, keys):
    from waggledance.core.bridge_log_reader import BridgeCursor
    if (type(value) is not dict or set(value) != keys or not _count(value["offset"], 0, 1 << 62)
            or type(value["file_identity"]) is not str or not 0 < len(value["file_identity"]) <= 4096
            or (value["generation"] is not None
                and (type(value["generation"]) is not str or not 0 < len(value["generation"]) <= 4096))):
        raise CliRefused()
    return BridgeCursor(offset=value["offset"], file_identity=value["file_identity"], generation=value["generation"])


def _pinned(document, name):
    item = document[name]
    if type(item) is not dict or set(item) != {"path", "sha256"}:
        raise CliRefused()
    return _read_pinned(item["path"], item["sha256"], _PIN_LIMITS[name])


def parse_cutoff(text):
    """The explicit ``YYYY-MM-DDTHH:MM:SSZ`` second as an aware UTC datetime; nothing else is accepted."""
    match = _CUTOFF.fullmatch(text) if type(text) is str else None
    if match is None:
        raise CliRefused()
    try:
        return datetime(*(int(part) for part in match.groups()), tzinfo=timezone.utc)
    except ValueError:
        raise CliRefused() from None


def build_inputs(config_bytes, expected_config_sha256):
    """Runner keyword inputs from pinned config bytes; reads each pinned file once."""
    from sandbox.mind.step2 import shared_producer as old
    from waggledance.core.bridge_log_reader import BridgePrefixPin
    if (type(config_bytes) is not bytes or not 0 < len(config_bytes) <= _CONFIG_LIMIT
            or type(expected_config_sha256) is not str or not _HEX64.match(expected_config_sha256)
            or not hmac.compare_digest(hashlib.sha256(config_bytes).hexdigest(), expected_config_sha256)):
        raise CliRefused()
    try:
        document = json.loads(config_bytes.decode("utf-8"), object_pairs_hook=_strict_object,
                              parse_constant=_refuse_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise CliRefused() from None
    if (type(document) is not dict or set(document) != _KEYS or document["schema"] != SCHEMA
            or not all(_absolute(document[key]) for key in ("source_path", "generation_path", "root",
                                                             "private_state_root", "trusted_model_root"))
            or not _count(document["max_bytes"], 1, _MAX_BYTES) or not _count(document["max_rows"], 1, _MAX_ROWS)):
        raise CliRefused()
    source_cursor = _cursor(document["source_cursor"], _CURSOR_KEYS)
    prefix = document["start_prefix"]
    if type(prefix) is not dict or set(prefix) != _PREFIX_KEYS or type(prefix["sha256"]) is not str \
            or not _HEX64.match(prefix["sha256"]):
        raise CliRefused()
    start = BridgePrefixPin(cursor=_cursor({key: prefix[key] for key in _CURSOR_KEYS}, _CURSOR_KEYS),
                            sha256=prefix["sha256"])
    raw = {name: _pinned(document, name) for name in ("plan", "anchor", "policy", "roster", "public_policy")}
    source = old.SourceConfig(source=document["source_path"], generation_path=document["generation_path"],
                              cursor=source_cursor, anchor_bytes=raw["anchor"],
                              anchor_sha256=document["anchor"]["sha256"], policy_bytes=raw["policy"],
                              policy_sha256=document["policy"]["sha256"], roster_bytes=raw["roster"],
                              roster_sha256=document["roster"]["sha256"], max_bytes=document["max_bytes"],
                              max_rows=document["max_rows"])
    config = old.PublicSourceConfig(source=source, public_policy_bytes=raw["public_policy"],
                                    public_policy_sha256=document["public_policy"]["sha256"])
    return {"plan_bytes": raw["plan"], "expected_plan_sha256": document["plan"]["sha256"], "config": config,
            "start_prefix": start, "root": document["root"], "private_state_root": document["private_state_root"],
            "trusted_model_root": document["trusted_model_root"]}


def _project(result):
    """The closed output document, or None when the runner result is not a complete report."""
    if (type(result) is not dict or set(result) != {"status", "clock", "cutoff_utc", "child"}
            or result["status"] != "INDEX_RUN_COMPLETE" or result["clock"] != "CALLER_UNVERIFIED"
            or type(result["cutoff_utc"]) is not str or type(result["child"]) is not dict
            or not all(key in result["child"] for key in _CHILD_KEYS)):
        return None
    return {"status": result["status"], "clock": result["clock"], "cutoff_utc": result["cutoff_utc"],
            "child": {key: result["child"][key] for key in _CHILD_KEYS}}


def invoke(*, enabled=False, config_bytes=None, expected_config_sha256=None, cutoff_utc=None):
    """One runner call. Raises ``CliRefused`` before the runner, or the runner's own outcome exceptions."""
    if enabled is not True:
        return {"status": "OFF"}
    cutoff = parse_cutoff(cutoff_utc)
    inputs = build_inputs(config_bytes, expected_config_sha256)
    from tools import wd_q4_index_clock_runner as runner
    return runner.run(enabled=True, caller_cutoff_utc=cutoff, **inputs)


def _parse_argv(argv):
    if "--enabled" not in argv:
        return None
    if argv.count("--enabled") != 1:
        raise CliRefused()
    rest = [item for item in argv if item != "--enabled"]
    if len(rest) != 2 * len(_FLAGS):
        raise CliRefused()
    values = {}
    for flag, value in zip(rest[::2], rest[1::2]):
        if flag not in _FLAGS or flag in values or type(value) is not str or value.startswith("--"):
            raise CliRefused()
        values[flag] = value
    return values


def _emit(stream, text):
    """Write all of ``text`` and flush: ``ok``, ``interrupted`` (KeyboardInterrupt) or ``failed`` (any other
    exception including SystemExit, or a short write). An exception's payload is discarded."""
    try:
        written = stream.write(text)
        stream.flush()
    except KeyboardInterrupt:
        return "interrupted"
    except BaseException:
        return "failed"
    return "ok" if written == len(text) else "failed"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    entered = False
    try:
        values = _parse_argv(argv)
        if values is None:
            outcome = _emit(sys.stdout, json.dumps({"status": "OFF"}) + "\n")
            if outcome == "ok":
                return 0
            if outcome == "interrupted":
                _emit(sys.stderr, "index_cli_interrupted\n")
                return 130
            _emit(sys.stderr, "index_cli_refused\n")
            return 2
        config_bytes = _read_pinned(values["--config"], values["--config-sha256"], _CONFIG_LIMIT)
        cutoff = parse_cutoff(values["--cutoff-utc"])
        inputs = build_inputs(config_bytes, values["--config-sha256"])
        from tools import wd_q4_index_clock_runner as runner
        entered = True
        try:
            result = runner.run(enabled=True, caller_cutoff_utc=cutoff, **inputs)
        except runner.IndexRunnerRefused:
            entered = False
            raise CliRefused() from None
        output = _project(result)
        if output is None:
            raise ValueError()
        text = json.dumps(output, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except KeyboardInterrupt:
        if not entered:
            _emit(sys.stderr, "index_cli_interrupted\n")
            return 130
        _emit(sys.stderr, "index_outcome_unknown\n")
        return 3
    except BaseException:
        # Includes SystemExit: its payload is never re-raised or printed, only the fixed token.
        if entered:
            _emit(sys.stderr, "index_outcome_unknown\n")
            return 3
        _emit(sys.stderr, "index_cli_refused\n")
        return 2
    # The runner may already have published: a failed or short receipt write is UNKNOWN, never success.
    if _emit(sys.stdout, text + "\n") != "ok":
        _emit(sys.stderr, "index_outcome_unknown\n")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
