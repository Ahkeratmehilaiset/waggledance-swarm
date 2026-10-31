"""Default-off pure validation for a caller-asserted public Q4 plan.

No files, environment, processes, network, models, or queries are accessed.
Hashes of supplied policy bytes are checked; pointer/source/encoder digests
remain unchecked caller claims. Filesystem paths are syntax only: no identity,
containment, freshness, reparse-point or same-user protection is established.
This module is not an activation, authorization, or production transport.
"""
from dataclasses import dataclass, field, fields
import ntpath
import re

from sandbox.mind.step2 import shared_capture_adapter as adapter
from tools.wd_q4_agent_query import VerifiedQ4Config, _validate_config

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ROOT = re.compile(r"[A-Za-z]:\\[^\\]+(?:\\[^\\]+)*\Z")
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.I)
# Keep identical to wd_q4_public_pointer_resolver._NAME; this is syntax only.
_GENERATION = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_CONFIG_PATHS = ("interpreter", "bootstrap", "runtime_root", "runtime_manifest", "memory", "descriptor",
                 "lock_file", "scratch_root")
_KEYS = frozenset({"schema", "config", "root", "expected_pointer_sha256", "expected_source_sha256",
    "expected_encoder_sha256", "policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256",
    "public_policy_bytes", "public_policy_sha256"})


class PublicPlanRefused(ValueError):
    """A fixed refusal that never includes caller input."""


@dataclass(frozen=True, slots=True)
class ValidatedPublicPlan:
    """Not a capability: any in-process caller can construct an instance.

    Syntactic/byte consistency only; target files and trust are not checked.
    """
    config: VerifiedQ4Config = field(repr=False)
    root: str = field(repr=False)
    expected_pointer_sha256: str
    expected_source_sha256: str
    expected_encoder_sha256: str
    policy_bytes: bytes = field(repr=False)
    policy_sha256: str
    roster_bytes: bytes = field(repr=False)
    roster_sha256: str
    public_policy_bytes: bytes = field(repr=False)
    public_policy_sha256: str
    schema: str = field(default="wd.q4-public-validated-plan.v1", init=False)
    status: str = field(default="VALIDATED_STRUCTURE_ONLY", init=False)
    authority: str = field(default="none", init=False)
    publisher_authentication: str = field(default="UNKNOWN", init=False)
    source_authentication: str = field(default="UNKNOWN", init=False)
    freshness: str = field(default="UNKNOWN", init=False)
    pointer_target: str = field(default="NOT_CHECKED", init=False)
    source_target: str = field(default="NOT_CHECKED", init=False)
    encoder_target: str = field(default="NOT_CHECKED", init=False)


def _root_syntax(value):
    if (type(value) is not str or len(value) > 32767 or _ROOT.fullmatch(value) is None
            or ntpath.normpath(value) != value or any(ord(c) < 32 or c in '<>:"/|?*' for c in value[2:])):
        raise ValueError()
    for part in value[3:].split("\\"):
        if part in (".", "..") or part.startswith(" ") or part.endswith((" ", ".")) or _RESERVED.fullmatch(part):
            raise ValueError()


def validate_public_plan(plan=None, *, enabled=False):
    """Validate in-memory shape and exact policy-byte pins; default is inert."""
    try:
        if type(enabled) is not bool:
            raise ValueError()
        if not enabled:
            return {"status": "OFF"}
        if type(plan) is not dict:
            raise ValueError()
        # Validate one private snapshot rather than rereading the caller's
        # mutable dictionary between policy checks and result construction.
        plan = plan.copy()
        if (set(plan) != _KEYS or type(plan["schema"]) is not str
                or plan["schema"] != "wd.q4-public-plan.v1"):
            raise ValueError()
        for name in ("expected_pointer_sha256", "expected_source_sha256", "expected_encoder_sha256",
                     "policy_sha256", "roster_sha256", "public_policy_sha256"):
            if type(plan[name]) is not str or _HEX.fullmatch(plan[name]) is None:
                raise ValueError()
        config = plan["config"]
        if type(config) is not VerifiedQ4Config:
            raise ValueError()
        for f in fields(config):
            if f.name != "timeout" and type(getattr(config, f.name)) is not str:
                raise ValueError()
        _validate_config(config)
        root = plan["root"]
        _root_syntax(root)
        for name in _CONFIG_PATHS:
            _root_syntax(getattr(config, name))
        if (_GENERATION.fullmatch(ntpath.basename(config.memory)) is None
                or ntpath.dirname(config.memory).casefold() != root.casefold()
                or ntpath.join(config.memory, "descriptor.json").casefold() != config.descriptor.casefold()):
            raise ValueError()
        tasks, _ = adapter.strict_pins(plan["policy_bytes"], plan["policy_sha256"],
                                       plan["roster_bytes"], plan["roster_sha256"])
        adapter.public_status_policy(plan["public_policy_bytes"], plan["public_policy_sha256"], tasks)
        return ValidatedPublicPlan(**{name: plan[name] for name in _KEYS if name != "schema"})
    except Exception:
        raise PublicPlanRefused("public_plan_refused") from None
