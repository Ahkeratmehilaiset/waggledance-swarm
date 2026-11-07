"""Default-off writer of the v2 public card plan and its synthetic route decision.

``write_card_plan`` reads ONE caller-pinned v1 public wrapper plan (``wd.q4-public-wrapper-plan.v1``, exact key set,
lowercase hex64 pins, the descriptor pin in ``config``), builds a synthetic ``wd.mind-card-route-decision.v1``
(``enable_synthetic_card_route``) from explicit caller pins plus the plan's own policy and roster pins and this
process's card module hash, verifies it with ``shared_card_projection.verify_route_decision`` and writes,
exclusively into one existing EMPTY absolute directory, ``card-decision.json`` (the canonical decision bytes) and
``public-plan.v2.json`` (the v1 plan unchanged plus ``schema = wd.q4-public-wrapper-plan.v2``,
``card_decision_path`` and ``card_decision_sha256``). The returned descriptor names both paths and pins; the caller
pins the plan digest where a v1 plan digest was pinned before.

What this does NOT establish: who issued the decision (unsigned, caller-pinned), that the decision pins match the
card build inputs (the card builder checks that), that the source prefix exists, freshness, a real (non-synthetic)
decision or any activation. Paths are checked for syntax, reparse points and emptiness only; same-user races stay
UNKNOWN. A refusal (``CardPlanRefused``) means nothing was written; ``CardPlanUnknown`` means a write was attempted
and the directory must be inspected before any retry. Both carry one fixed token and no input content.

``write_staged_card_plan`` writes the v3 staged plan (``wd.q4-public-wrapper-plan.v3``) for an EXISTING staged C3
card generation; it builds no decision. The pin chain starts at the caller-pinned v1 plan: the snapshot pointer is
read under the plan ``pointer_sha256``, the card decision under the pointer ``decision_sha256``, the card policy
under the decision ``card_policy_sha256`` and the non-review policy and roster under the plan pins. The end cursor is
the pointer's source prefix (offset, generation) plus the real source file identity. The real staged consumer
``wd_q4_public_pointer_resolver.resolve_card_snapshot_pointer`` then validates exactly these inputs (snapshot
artifacts, decision, card policy, source prefix bytes, identity and generation marker) and its source prefix sha256
must equal the plan ``source_anchor_sha256`` (the staged query checks that). Only ``public-plan.v3.json`` is
written, exclusively into one existing empty absolute directory: the v1 plan plus the decision, card policy,
pointer, source and generation marker paths, the decision and card policy pins and the end cursor. It references the
caller's files, it copies none. Freshness, issuer and source authentication and same-user races stay UNKNOWN; a
later append to the source keeps the frozen prefix cursor valid.

Command line: no arguments prints ``{"status":"OFF"}`` and reads nothing; ``--enabled --request PATH
--request-sha256 HEX`` reads one pinned closed request and prints the descriptor as one canonical JSON line. The
request schema selects the writer: ``wd.q4-public-card-plan-request.v1`` (v2 plan) or
``wd.q4-public-staged-card-plan-request.v1`` (v3 plan; keys public_plan_path, public_plan_sha256, output_dir,
pointer_path, card_decision_path, card_policy_path, source_path, generation_path). A refusal prints one fixed line
to stderr and exits 2 (unknown: 3).
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from sandbox.mind.step2 import shared_card_projection as cards
from tools import wd_q4_descriptor_pointer as paths
from tools import wd_q4_lane_link as link

__all__ = ["CardPlanRefused", "CardPlanUnknown", "write_card_plan", "write_staged_card_plan", "main", "PLAN_V1",
           "PLAN_V2", "PLAN_V3", "REQUEST_SCHEMA", "RESULT_SCHEMA", "STAGED_REQUEST_SCHEMA", "STAGED_RESULT_SCHEMA",
           "DECISION_NAME", "PLAN_NAME", "STAGED_PLAN_NAME"]

PLAN_V1 = "wd.q4-public-wrapper-plan.v1"
PLAN_V2 = "wd.q4-public-wrapper-plan.v2"
REQUEST_SCHEMA = "wd.q4-public-card-plan-request.v1"
RESULT_SCHEMA = "wd.q4-public-card-plan-written.v1"
DECISION_NAME = "card-decision.json"
PLAN_NAME = "public-plan.v2.json"
PLAN_V3 = "wd.q4-public-wrapper-plan.v3"
STAGED_REQUEST_SCHEMA = "wd.q4-public-staged-card-plan-request.v1"
STAGED_RESULT_SCHEMA = "wd.q4-public-staged-card-plan-written.v1"
STAGED_PLAN_NAME = "public-plan.v3.json"
_LIMIT = 65536
_MIB = 1048576
_DECISION_LIMIT = 262144  # the read limits of Invoke-WdQ4Query.ps1 Get-WdQ4PublicPlan
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PLAN_PINS = ("pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256", "roster_sha256",
              "public_policy_sha256")
_PLAN_PATHS = ("root", "policy_path", "roster_path", "public_policy_path")
_CARD_PINS = ("card_policy_sha256", "vocabulary_sha256", "path_allowlist_sha256")
_REQUEST_KEYS = frozenset({"schema", "public_plan_path", "public_plan_sha256", "output_dir", "source_prefix",
                           "observed_heads", *_CARD_PINS})
_STAGED_PATHS = ("pointer_path", "card_decision_path", "card_policy_path", "source_path", "generation_path")
_STAGED_REQUEST_KEYS = frozenset({"schema", "public_plan_path", "public_plan_sha256", "output_dir", *_STAGED_PATHS})
_REFUSAL = '{"error":"card_plan_refused"}\n'
_UNKNOWN = '{"error":"card_plan_unknown"}\n'


class CardPlanRefused(ValueError):
    """Nothing was written; fixed token ``card_plan_refused``."""


class CardPlanUnknown(RuntimeError):
    """A write was attempted; fixed token ``card_plan_unknown``. Inspect the output directory before a retry."""


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _unique(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError()
        document[key] = value
    return document


def _constant(_value):
    raise ValueError()


def _json(raw):
    return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_unique, parse_constant=_constant)


def _hex(value):
    return type(value) is str and _HEX64.fullmatch(value) is not None


def _path(value):
    path = Path(paths._absolute(value))
    paths._chain(path)
    return path


def _read_pinned(value, expected, limit=_LIMIT):
    if not _hex(expected):
        raise ValueError()
    path = _path(value)
    if not path.is_file():
        raise ValueError()
    with open(path, "rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit or _sha(raw) != expected:
        raise ValueError()
    return raw


def _exclusive(path, raw):
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        if stream.write(raw) != len(raw):
            raise OSError()
        stream.flush()
        os.fsync(stream.fileno())


def _v1_plan(raw):
    plan = _json(raw)
    if (type(plan) is not dict or set(plan) != link._PUBLIC_PLAN_SCHEMAS[PLAN_V1] or plan["schema"] != PLAN_V1
            or type(plan["config"]) is not dict or not _hex(plan["config"].get("descriptor_sha256"))
            or not all(_hex(plan[key]) for key in _PLAN_PINS)
            or not all(type(plan[key]) is str and plan[key] for key in _PLAN_PATHS)):
        raise ValueError()
    return plan


def _decision(plan, card_pins, source_prefix, observed_heads):
    # the card pins, the prefix and the heads are checked by verify_route_decision below (the one authority)
    if type(source_prefix) is not dict or (observed_heads is not None and type(observed_heads) is not list):
        raise ValueError()
    document = {"schema": cards.DECISION_SCHEMA, "decision": cards.DECISION_NAME, "synthetic": True,
                "projection_schema": cards.SCHEMA, "scope": cards.SHARED_SCOPE,
                "card_module_sha256": cards.module_sha256(), "nonreview_policy_sha256": plan["policy_sha256"],
                "roster_sha256": plan["roster_sha256"], **card_pins, "source_prefix": dict(source_prefix),
                "observed_heads": None if observed_heads is None else list(observed_heads)}
    raw = _encode(document)
    cards.verify_route_decision(raw, _sha(raw), policy_sha256=plan["policy_sha256"],
                                roster_sha256=plan["roster_sha256"])  # SharedCardRefused is a ValueError
    return raw


def write_card_plan(*, enabled=False, public_plan_path=None, expected_public_plan_sha256=None, output_dir=None,
                    card_policy_sha256=None, vocabulary_sha256=None, path_allowlist_sha256=None,
                    source_prefix=None, observed_heads=None):
    """Write the synthetic decision and the v2 plan; see the module doc. ``enabled`` must be the bool ``True``."""
    if enabled is False:
        return {"status": "OFF"}
    attempted = failed = False
    result = None
    try:
        if enabled is not True:
            raise ValueError()
        base_raw = _read_pinned(public_plan_path, expected_public_plan_sha256)
        plan = _v1_plan(base_raw)
        decision_raw = _decision(plan, {"card_policy_sha256": card_policy_sha256,
                                        "vocabulary_sha256": vocabulary_sha256,
                                        "path_allowlist_sha256": path_allowlist_sha256},
                                 source_prefix, observed_heads)
        out = _path(output_dir)
        if not out.is_dir() or any(out.iterdir()):
            raise ValueError()
        decision_path, plan_path = out / DECISION_NAME, out / PLAN_NAME
        card_plan = dict(plan, schema=PLAN_V2, card_decision_path=str(decision_path),
                         card_decision_sha256=_sha(decision_raw))
        plan_raw = _encode(card_plan)
        if len(plan_raw) > _LIMIT:  # the key set is the exact v1 set plus the two card keys
            raise ValueError()
        attempted = True
        _exclusive(decision_path, decision_raw)
        _exclusive(plan_path, plan_raw)
        if (_read_pinned(str(decision_path), _sha(decision_raw)) != decision_raw
                or _read_pinned(str(plan_path), _sha(plan_raw)) != plan_raw):
            raise ValueError()
        result = {"schema": RESULT_SCHEMA, "status": "WRITTEN", "base_public_plan_sha256": expected_public_plan_sha256,
                  "public_plan_path": str(plan_path), "public_plan_sha256": _sha(plan_raw),
                  "card_decision_path": str(decision_path), "card_decision_sha256": _sha(decision_raw),
                  "decision": cards.DECISION_NAME, "authority": "none", "issuer_authentication": "UNKNOWN"}
    except Exception:
        failed = True
    if failed:
        if attempted:
            raise CardPlanUnknown("card_plan_unknown")
        raise CardPlanRefused("card_plan_refused")
    return result


def _staged_plan(plan, pointer_path, card_decision_path, card_policy_path, source_path, generation_path):
    from tools import wd_q4_public_pointer_resolver as resolver
    from waggledance.core import bridge_log_reader as bridge

    pointer_raw = _read_pinned(pointer_path, plan["pointer_sha256"], _MIB)
    pointer = _json(pointer_raw)
    decision_raw = _read_pinned(card_decision_path, pointer["decision_sha256"], _DECISION_LIMIT)
    card_policy_raw = _read_pinned(card_policy_path, _json(decision_raw)["card_policy_sha256"])
    source, marker = _path(source_path), _path(generation_path)
    with bridge._open_log(source) as stream:  # the same identity the prefix verifier compares
        identity = bridge._file_identity(stream)
    prefix = pointer["source_prefix"]
    cursor = bridge.BridgeCursor(prefix["offset"], identity, prefix["generation"])
    # the real staged consumer is the one authority for the tuple; it refuses any partial or mismatched input
    binding = resolver.resolve_card_snapshot_pointer(
        plan["root"], enabled=True, pointer_bytes=pointer_raw, expected_pointer_sha256=plan["pointer_sha256"],
        policy_bytes=_read_pinned(plan["policy_path"], plan["policy_sha256"]), policy_sha256=plan["policy_sha256"],
        roster_bytes=_read_pinned(plan["roster_path"], plan["roster_sha256"], _MIB),
        roster_sha256=plan["roster_sha256"], card_decision_bytes=decision_raw,
        card_decision_sha256=_sha(decision_raw), card_policy_bytes=card_policy_raw, source_path=str(source),
        generation_path=str(marker), end_cursor=cursor)
    if binding["source_prefix"]["sha256"] != plan["source_anchor_sha256"]:  # query_staged_card_snapshot checks it
        raise ValueError()
    staged = dict(plan, schema=PLAN_V3, card_decision_path=str(_path(card_decision_path)),
                  card_decision_sha256=_sha(decision_raw), card_policy_path=str(_path(card_policy_path)),
                  card_policy_sha256=_sha(card_policy_raw), pointer_path=str(_path(pointer_path)),
                  source_path=str(source), generation_path=str(marker),
                  end_cursor={"offset": cursor.offset, "file_identity": cursor.file_identity,
                              "generation": cursor.generation})
    return staged, binding["source_prefix"]


def write_staged_card_plan(*, enabled=False, public_plan_path=None, expected_public_plan_sha256=None,
                           output_dir=None, pointer_path=None, card_decision_path=None, card_policy_path=None,
                           source_path=None, generation_path=None):
    """Write the v3 staged plan for an existing staged generation; see the module doc. ``enabled`` must be True."""
    if enabled is False:
        return {"status": "OFF"}
    attempted = failed = False
    result = None
    try:
        if enabled is not True:
            raise ValueError()
        plan = _v1_plan(_read_pinned(public_plan_path, expected_public_plan_sha256))
        staged, source_prefix = _staged_plan(plan, pointer_path, card_decision_path, card_policy_path,
                                             source_path, generation_path)
        out = _path(output_dir)
        if not out.is_dir() or any(out.iterdir()):
            raise ValueError()
        plan_path = out / STAGED_PLAN_NAME
        plan_raw = _encode(staged)
        if len(plan_raw) > _LIMIT:
            raise ValueError()
        attempted = True
        _exclusive(plan_path, plan_raw)
        if _read_pinned(str(plan_path), _sha(plan_raw)) != plan_raw:
            raise ValueError()
        result = {"schema": STAGED_RESULT_SCHEMA, "status": "WRITTEN",
                  "base_public_plan_sha256": expected_public_plan_sha256, "public_plan_path": str(plan_path),
                  "public_plan_sha256": _sha(plan_raw), "pointer_sha256": staged["pointer_sha256"],
                  "card_decision_sha256": staged["card_decision_sha256"],
                  "card_policy_sha256": staged["card_policy_sha256"], "source_prefix": source_prefix,
                  "end_cursor": staged["end_cursor"], "authority": "none", "issuer_authentication": "UNKNOWN"}
    except Exception:
        failed = True
    if failed:
        if attempted:
            raise CardPlanUnknown("card_plan_unknown")
        raise CardPlanRefused("card_plan_refused")
    return result


def _request(path, pin):
    document = _json(_read_pinned(path, pin))
    if type(document) is not dict:
        raise ValueError()
    if document.get("schema") == STAGED_REQUEST_SCHEMA:
        if set(document) != _STAGED_REQUEST_KEYS:
            raise ValueError()
        return write_staged_card_plan, {
            "public_plan_path": document["public_plan_path"],
            "expected_public_plan_sha256": document["public_plan_sha256"], "output_dir": document["output_dir"],
            **{key: document[key] for key in _STAGED_PATHS}}
    if set(document) != _REQUEST_KEYS or document["schema"] != REQUEST_SCHEMA:
        raise ValueError()
    return write_card_plan, {"public_plan_path": document["public_plan_path"],
                             "expected_public_plan_sha256": document["public_plan_sha256"],
                             "output_dir": document["output_dir"], "source_prefix": document["source_prefix"],
                             "observed_heads": document["observed_heads"],
                             **{key: document[key] for key in _CARD_PINS}}


def _fail(text, code):
    try:
        sys.stderr.write(text)
        sys.stderr.flush()
    except Exception:
        pass
    return code


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv == []:
        sys.stdout.write('{"status":"OFF"}\n')
        return 0
    try:
        if (len(argv) != 5 or argv[0] != "--enabled" or argv[1] != "--request" or argv[3] != "--request-sha256"):
            raise CardPlanRefused("card_plan_refused")
        try:
            request = _request(argv[2], argv[4])
        except Exception:
            request = None
        if request is None:
            raise CardPlanRefused("card_plan_refused")
        writer, arguments = request
        result = writer(enabled=True, **arguments)
        sys.stdout.write(_encode(result).decode("ascii") + "\n")
        sys.stdout.flush()
        return 0
    except CardPlanUnknown:
        return _fail(_UNKNOWN, 3)
    except Exception:
        return _fail(_REFUSAL, 2)


if __name__ == "__main__":
    sys.exit(main())
