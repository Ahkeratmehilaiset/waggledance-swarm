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

Command line: no arguments prints ``{"status":"OFF"}`` and reads nothing; ``--enabled --request PATH
--request-sha256 HEX`` reads one pinned closed request (``wd.q4-public-card-plan-request.v1``) and prints the
descriptor as one canonical JSON line; a refusal prints one fixed line to stderr and exits 2 (unknown: 3).
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

__all__ = ["CardPlanRefused", "CardPlanUnknown", "write_card_plan", "main", "PLAN_V1", "PLAN_V2",
           "REQUEST_SCHEMA", "RESULT_SCHEMA", "DECISION_NAME", "PLAN_NAME"]

PLAN_V1 = "wd.q4-public-wrapper-plan.v1"
PLAN_V2 = "wd.q4-public-wrapper-plan.v2"
REQUEST_SCHEMA = "wd.q4-public-card-plan-request.v1"
RESULT_SCHEMA = "wd.q4-public-card-plan-written.v1"
DECISION_NAME = "card-decision.json"
PLAN_NAME = "public-plan.v2.json"
_LIMIT = 65536
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PLAN_PINS = ("pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256", "roster_sha256",
              "public_policy_sha256")
_PLAN_PATHS = ("root", "policy_path", "roster_path", "public_policy_path")
_CARD_PINS = ("card_policy_sha256", "vocabulary_sha256", "path_allowlist_sha256")
_REQUEST_KEYS = frozenset({"schema", "public_plan_path", "public_plan_sha256", "output_dir", "source_prefix",
                           "observed_heads", *_CARD_PINS})
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


def _read_pinned(value, expected):
    if not _hex(expected):
        raise ValueError()
    path = _path(value)
    if not path.is_file():
        raise ValueError()
    with open(path, "rb") as stream:
        raw = stream.read(_LIMIT + 1)
    if len(raw) > _LIMIT or _sha(raw) != expected:
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


def _request(path, pin):
    document = _json(_read_pinned(path, pin))
    if type(document) is not dict or set(document) != _REQUEST_KEYS or document["schema"] != REQUEST_SCHEMA:
        raise ValueError()
    return {"public_plan_path": document["public_plan_path"],
            "expected_public_plan_sha256": document["public_plan_sha256"], "output_dir": document["output_dir"],
            "source_prefix": document["source_prefix"], "observed_heads": document["observed_heads"],
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
        result = write_card_plan(enabled=True, **request)
        sys.stdout.write(_encode(result).decode("ascii") + "\n")
        sys.stdout.flush()
        return 0
    except CardPlanUnknown:
        return _fail(_UNKNOWN, 3)
    except Exception:
        return _fail(_REFUSAL, 2)


if __name__ == "__main__":
    sys.exit(main())
