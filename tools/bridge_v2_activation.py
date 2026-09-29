"""Bridge v2 activation foundation (F0): signed-policy loader and fail-closed feature gate.

Pure and read-only. Nothing here writes, launches, or calls the network, and no
production caller uses it yet. Every new Bridge v2 component is meant to ask
``feature_enabled(name, ...)`` immediately before a dispatch or side effect.

Decision inputs, all re-read on EVERY call (no caching):

1. The activation policy ``configs/bridge_v2_activation.json`` (schema
   ``wd.bridge-v2-activation.v1``). It is enforced only when its canonical
   policy digest equals the digest the CALLER obtained from the operator-signed
   packet (``trusted_policy_sha256``). This module never creates, infers or
   defaults that digest: without it every feature is disabled. The in-file
   ``signature`` record is a binding (digest, head, tree, expiry), not proof of
   authenticity; authenticity comes from the caller's trusted digest.
2. The durable, versioned revocation and freeze state
   ``<runtime_root>/bridge_v2/revocation.json`` (schema
   ``wd.bridge-v2-revocation.v1``). Missing, corrupt, bound to another policy,
   older than the caller's known minimum version, future-dated, or older than
   the policy's maximum age: disabled. A freeze disables everything; a revoked
   feature is disabled.
3. ``WAGGLE_BRIDGE_V2_ENABLED``: deny-only. Any value other than ``1``/``true``
   /``on``/``yes`` denies; no value can grant. It is an extra local deny, never
   the fleet-wide switch (running processes never see a changed variable).

Runtime stage state (``<runtime_root>/bridge_v2/stage_state.json``) records
progress only and is deliberately NOT an input: it can never grant a flag.

Caller pins: an ENABLED outcome also requires ``expected_head``/``expected_tree`` (from the
deployed bundle) and ``min_revocation_version`` (the caller's persisted high-water mark).
Dependencies are transitive: a revoked or disabled feature anywhere in the ``requires``
closure disables its descendants, and dependency cycles are rejected at validation. An
enabling policy must set both ``expires_utc`` and ``revocation_max_age_seconds``.

Expiry: after the policy's absolute ``expires_utc`` every feature is disabled.
The plan's carry-over for a stage that already met its coverage predicate (R15)
needs a signed qualification record that does not exist yet; until it does,
expiry fails closed. Unknown stays unknown.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Mapping

POLICY_SCHEMA = "wd.bridge-v2-activation.v1"
SIGNATURE_SCHEMA = "wd.bridge-v2-activation-signature.v1"
REVOCATION_SCHEMA = "wd.bridge-v2-revocation.v1"
KILL_SWITCH_ENV = "WAGGLE_BRIDGE_V2_ENABLED"
KILL_SWITCH_PASS_VALUES = ("1", "true", "on", "yes")
REVOCATION_RELATIVE = Path("bridge_v2") / "revocation.json"

MAX_FILE_BYTES = 256 * 1024
MAX_FUTURE_SKEW = timedelta(minutes=5)
FEATURE_NAME = re.compile(r"F(?:[1-9]|[12][0-9]|30)")
POLICY_BITS = ("rule8_amendment", "f19_routing_policy", "f24_composer_rule", "learning_2_11")
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX40 = re.compile(r"[0-9a-f]{40}")
UTC_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?Z")

POLICY_KEYS = {"schema", "policy_version", "expires_utc", "revocation_max_age_seconds",
               "features", "policy_bits", "policy_bit_requires", "parameters"}
FEATURE_KEYS = {"enabled", "stage", "requires", "requires_bits"}
SIGNATURE_KEYS = {"schema", "policy_sha256", "head", "tree", "signed_utc", "expires_utc"}
REVOCATION_KEYS = {"schema", "version", "policy_sha256", "frozen", "revoked", "updated_utc"}


class ActivationError(ValueError):
    """The activation inputs are missing, malformed or inconsistent (always fail closed)."""


@dataclass(frozen=True)
class Decision:
    feature: str
    enabled: bool
    reason: str
    policy_sha256: str | None = None
    revocation_version: int | None = None


# ---------------------------------------------------------------------------
# Strict parsing helpers
# ---------------------------------------------------------------------------

def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ActivationError("duplicate JSON key: " + str(key)[:64])
        result[key] = value
    return result


def _reject_constant(value):
    raise ActivationError("non-finite JSON constant: " + value)


def _read_json(path: Path, what: str):
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)  # bounded: never reads an oversized file whole
    except FileNotFoundError as exc:
        raise ActivationError(what + " is missing") from exc
    except OSError as exc:
        raise ActivationError(what + " is unreadable: " + type(exc).__name__) from exc
    if len(raw) > MAX_FILE_BYTES:
        raise ActivationError(what + " exceeds " + str(MAX_FILE_BYTES) + " bytes")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                          parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ActivationError(what + " is not strict UTF-8 JSON") from exc


def _exact_keys(value, keys: set[str], what: str) -> dict:
    if not isinstance(value, dict):
        raise ActivationError(what + " must be an object")
    if set(value) != keys:
        missing, extra = sorted(keys - set(value)), sorted(set(value) - keys)
        raise ActivationError(what + " keys differ (missing " + ",".join(missing) +
                              "; unknown " + ",".join(extra)[:200] + ")")
    return value


def _is_int(value) -> bool:
    return type(value) is int


def _parse_utc(value, what: str) -> datetime:
    if not isinstance(value, str) or not UTC_STAMP.fullmatch(value):
        raise ActivationError(what + " must be an absolute UTC timestamp ending in Z")
    text = value[:-1]
    if "." in text:
        head, frac = text.split(".")
        text = head + "." + (frac + "000000")[:6]
    try:
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ActivationError(what + " is not a valid timestamp") from exc


def canonical_sha256(value) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _check_parameters(value, depth: int = 0) -> None:
    """Signed parameters are opaque here, but must be plain finite JSON of bounded depth."""
    if depth > 8:
        raise ActivationError("parameters nest too deeply")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ActivationError("parameter keys must be strings")
            _check_parameters(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_parameters(item, depth + 1)
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise ActivationError("parameters hold a non-JSON value")


def _reject_cycles(graph: dict, what: str) -> None:
    """Iterative depth-first search; any dependency cycle is invalid (no recursion, no hang)."""
    state = dict.fromkeys(graph, 0)  # 0 unvisited, 1 on the current path, 2 done
    for start in graph:
        if state[start]:
            continue
        state[start] = 1
        stack = [(start, iter(graph[start]))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                state[node] = 2
                stack.pop()
            elif state.get(child) == 1:
                raise ActivationError(what + " contain a dependency cycle through " + str(child)[:32])
            elif state.get(child) == 0:
                state[child] = 1
                stack.append((child, iter(graph[child])))


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

def validate_policy(policy) -> dict:
    """Strict schema and dependency validation. Raises ActivationError; returns the policy."""
    _exact_keys(policy, POLICY_KEYS, "policy")
    if policy["schema"] != POLICY_SCHEMA:
        raise ActivationError("policy schema must be " + POLICY_SCHEMA)
    if not _is_int(policy["policy_version"]) or policy["policy_version"] < 1:
        raise ActivationError("policy_version must be an integer >= 1")
    if policy["expires_utc"] is not None:
        _parse_utc(policy["expires_utc"], "expires_utc")
    age = policy["revocation_max_age_seconds"]
    if age is not None and (not _is_int(age) or not 60 <= age <= 30 * 86400):
        raise ActivationError("revocation_max_age_seconds must be null or an integer 60..2592000")

    bits = _exact_keys(policy["policy_bits"], set(POLICY_BITS), "policy_bits")
    if not all(type(v) is bool for v in bits.values()):
        raise ActivationError("policy_bits values must be booleans")
    bit_requires = policy["policy_bit_requires"]
    if not isinstance(bit_requires, dict) or not set(bit_requires) <= set(POLICY_BITS):
        raise ActivationError("policy_bit_requires must map known policy bits")
    for bit, needs in bit_requires.items():
        if not isinstance(needs, list) or not all(n in POLICY_BITS and n != bit for n in needs) \
                or len(set(needs)) != len(needs):
            raise ActivationError("policy_bit_requires." + bit + " must list other known bits once")
        if bits[bit] and not all(bits[n] for n in needs):
            raise ActivationError("policy bit " + bit + " is on without its required bits")

    features = policy["features"]
    if not isinstance(features, dict) or not features:
        raise ActivationError("features must be a non-empty object")
    for name, spec in features.items():
        if not FEATURE_NAME.fullmatch(name):
            raise ActivationError("unknown feature name: " + str(name)[:32])
        _exact_keys(spec, FEATURE_KEYS, "features." + name)
        if type(spec["enabled"]) is not bool:
            raise ActivationError("features." + name + ".enabled must be a boolean")
        stage = spec["stage"]
        if stage is not None and (not _is_int(stage) or not 1 <= stage <= 5):
            raise ActivationError("features." + name + ".stage must be null or 1..5")
        if spec["enabled"] and stage is None:
            raise ActivationError("features." + name + " is enabled without an assigned stage")
        requires, requires_bits = spec["requires"], spec["requires_bits"]
        if not isinstance(requires, list) or len(set(map(str, requires))) != len(requires) \
                or not all(isinstance(r, str) and r in features and r != name for r in requires):
            raise ActivationError("features." + name + ".requires must list other declared features once")
        if not isinstance(requires_bits, list) or len(set(map(str, requires_bits))) != len(requires_bits) \
                or not all(b in POLICY_BITS for b in requires_bits):
            raise ActivationError("features." + name + ".requires_bits must list known policy bits once")
        if spec["enabled"]:
            if not all(features[r]["enabled"] for r in requires):
                raise ActivationError("feature " + name + " is enabled without its required features")
            if not all(bits[b] for b in requires_bits):
                raise ActivationError("feature " + name + " is enabled without its required policy bits")
    _reject_cycles({name: spec["requires"] for name, spec in features.items()}, "features.requires")
    _reject_cycles({bit: bit_requires.get(bit, []) for bit in POLICY_BITS}, "policy_bit_requires")
    if any(spec["enabled"] for spec in features.values()) or any(bits.values()):
        # An enabling policy must be bounded in time and in revocation freshness (fail closed).
        if policy["expires_utc"] is None:
            raise ActivationError("an enabling policy must set an absolute expires_utc")
        if age is None:
            raise ActivationError("an enabling policy must set revocation_max_age_seconds")
    if not isinstance(policy["parameters"], dict):
        raise ActivationError("parameters must be an object")
    _check_parameters(policy["parameters"])
    return policy


def validate_signature(signature, policy_sha256: str, *, expected_head: str | None = None,
                       expected_tree: str | None = None) -> dict:
    _exact_keys(signature, SIGNATURE_KEYS, "signature")
    if signature["schema"] != SIGNATURE_SCHEMA:
        raise ActivationError("signature schema must be " + SIGNATURE_SCHEMA)
    for key, pattern in (("policy_sha256", HEX64), ("head", HEX40), ("tree", HEX40)):
        if not isinstance(signature[key], str) or not pattern.fullmatch(signature[key]):
            raise ActivationError("signature." + key + " must be lowercase hex")
    if signature["policy_sha256"] != policy_sha256:
        raise ActivationError("signature does not bind this policy digest")
    if expected_head is not None and signature["head"] != expected_head:
        raise ActivationError("signature head differs from the expected head")
    if expected_tree is not None and signature["tree"] != expected_tree:
        raise ActivationError("signature tree differs from the expected tree")
    signed = _parse_utc(signature["signed_utc"], "signature.signed_utc")
    if signed >= _parse_utc(signature["expires_utc"], "signature.expires_utc"):
        raise ActivationError("signature.signed_utc must be earlier than signature.expires_utc")
    return signature


def load_policy(config_path: Path, trusted_policy_sha256: str | None, *,
                expected_head: str | None = None, expected_tree: str | None = None) -> tuple[dict, str, dict]:
    """Load and validate the signed policy from ONE read. Raises ActivationError unless fully bound."""
    document = _exact_keys(_read_json(Path(config_path), "activation config"),
                           {"policy", "signature"}, "activation config")
    policy = validate_policy(document["policy"])
    digest = canonical_sha256(policy)
    if document["signature"] is None:
        raise ActivationError("activation policy is unsigned")
    if not isinstance(trusted_policy_sha256, str) or not HEX64.fullmatch(trusted_policy_sha256):
        raise ActivationError("no trusted policy digest from the signed packet")
    if trusted_policy_sha256 != digest:
        raise ActivationError("policy digest differs from the trusted signed digest")
    signature = validate_signature(document["signature"], digest,
                                   expected_head=expected_head, expected_tree=expected_tree)
    if policy["expires_utc"] is not None and (
            _parse_utc(signature["expires_utc"], "signature.expires_utc")
            > _parse_utc(policy["expires_utc"], "policy expires_utc")):
        raise ActivationError("signature outlives the policy's absolute expiry")
    return policy, digest, signature


# ---------------------------------------------------------------------------
# Revocation and freeze
# ---------------------------------------------------------------------------

def load_revocation(runtime_root: Path, policy: dict, policy_sha256: str, now: datetime, *,
                    min_version: int | None = None) -> dict:
    path = Path(runtime_root) / REVOCATION_RELATIVE
    state = _exact_keys(_read_json(path, "revocation state"), REVOCATION_KEYS, "revocation state")
    if state["schema"] != REVOCATION_SCHEMA:
        raise ActivationError("revocation schema must be " + REVOCATION_SCHEMA)
    if not _is_int(state["version"]) or state["version"] < 1:
        raise ActivationError("revocation version must be an integer >= 1")
    if min_version is not None and state["version"] < min_version:
        raise ActivationError("revocation state is older than the known version (rollback)")
    if state["policy_sha256"] != policy_sha256:
        raise ActivationError("revocation state is bound to another policy")
    if type(state["frozen"]) is not bool:
        raise ActivationError("revocation frozen must be a boolean")
    revoked = state["revoked"]
    if not isinstance(revoked, list) or not all(isinstance(r, str) and FEATURE_NAME.fullmatch(r) for r in revoked):
        raise ActivationError("revocation revoked must list feature names")
    updated = _parse_utc(state["updated_utc"], "revocation updated_utc")
    if updated - now > MAX_FUTURE_SKEW:
        raise ActivationError("revocation state is dated in the future")
    age = policy["revocation_max_age_seconds"]
    if age is not None and now - updated > timedelta(seconds=age):
        raise ActivationError("revocation state is stale")
    return state


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

def kill_switch_denies(environ: Mapping[str, str] | None = None) -> bool:
    """Deny-only: unset means no local deny; any value except an explicit pass denies."""
    env = os.environ if environ is None else environ
    if KILL_SWITCH_ENV not in env:
        return False
    return str(env[KILL_SWITCH_ENV]).strip().lower() not in KILL_SWITCH_PASS_VALUES


def _blocked_dependency(feature: str, features: dict, revoked: set) -> str | None:
    """First dependency in the transitive requires closure that is revoked or not enabled.

    Iterative with a visited set, so it terminates even if a cycle ever slipped through."""
    seen, stack = set(), list(features[feature]["requires"])
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        spec = features.get(name)
        if spec is None or not spec["enabled"] or name in revoked:
            return name
        stack.extend(spec["requires"])
    return None


def evaluate(feature: str, *, config_path: Path, runtime_root: Path, trusted_policy_sha256: str | None,
             now: datetime | None = None, environ: Mapping[str, str] | None = None,
             min_revocation_version: int | None = None, expected_head: str | None = None,
             expected_tree: str | None = None) -> Decision:
    """Full decision for one feature. Never raises; every failure is a disabled Decision.

    An ENABLED outcome additionally requires caller-owned pins: ``expected_head`` and
    ``expected_tree`` (40-hex, from the deployed bundle, matched against the signature)
    and ``min_revocation_version`` (the highest revocation version the caller has
    persisted; rollback protection). ``trusted_policy_sha256`` must come from the
    operator-signed packet and is the canonical digest of the PARSED policy, not of the
    file bytes; a caller must never take it from this config file (for example from
    ``signature.policy_sha256``), because that would let the file authorize itself.
    Callers persist ``Decision.revocation_version`` as their new high-water mark."""
    if not isinstance(feature, str) or not FEATURE_NAME.fullmatch(feature):
        return Decision(str(feature)[:32], False, "unknown feature name")
    if kill_switch_denies(environ):
        return Decision(feature, False, "local kill switch " + KILL_SWITCH_ENV + " denies")
    current = datetime.now(timezone.utc) if now is None else now
    if current.tzinfo is None:
        return Decision(feature, False, "decision time must be timezone-aware")
    current = current.astimezone(timezone.utc)
    try:
        policy, digest, signature = load_policy(config_path, trusted_policy_sha256,
                                     expected_head=expected_head, expected_tree=expected_tree)
    except (ActivationError, ValueError, TypeError, AttributeError, KeyError) as exc:
        return Decision(feature, False, "policy: " + str(exc)[:200])
    try:
        if policy["expires_utc"] is None:
            return Decision(feature, False, "policy has no absolute expiry", digest)
        if current >= _parse_utc(policy["expires_utc"], "policy expires_utc"):
            return Decision(feature, False, "policy expired", digest)
        if current >= _parse_utc(signature["expires_utc"], "signature.expires_utc"):
            return Decision(feature, False, "signature expired", digest)
        if _parse_utc(signature["signed_utc"], "signature.signed_utc") - current > MAX_FUTURE_SKEW:
            return Decision(feature, False, "signature is dated in the future", digest)
        state = load_revocation(runtime_root, policy, digest, current, min_version=min_revocation_version)
    except (ActivationError, ValueError, TypeError, AttributeError, KeyError) as exc:
        return Decision(feature, False, "revocation/expiry: " + str(exc)[:200], digest)
    if state["frozen"]:
        return Decision(feature, False, "fleet freeze is active", digest, state["version"])
    if feature in state["revoked"]:
        return Decision(feature, False, "feature revoked", digest, state["version"])
    spec = policy["features"].get(feature)
    if spec is None:
        return Decision(feature, False, "feature not declared in the signed policy", digest, state["version"])
    if not spec["enabled"]:
        return Decision(feature, False, "feature off in the signed policy", digest, state["version"])
    blocked = _blocked_dependency(feature, policy["features"], set(state["revoked"]))
    if blocked is not None:
        return Decision(feature, False, "required feature " + blocked + " revoked or disabled (transitive)",
                        digest, state["version"])
    if not all(isinstance(pin, str) and HEX40.fullmatch(pin) for pin in (expected_head, expected_tree)):
        return Decision(feature, False, "caller did not pin the expected head and tree", digest, state["version"])
    if type(min_revocation_version) is not int or min_revocation_version < 1:
        return Decision(feature, False, "caller did not pass its persisted revocation high-water version",
                        digest, state["version"])
    return Decision(feature, True, "enabled by the signed policy", digest, state["version"])


def feature_enabled(feature: str, **kwargs) -> bool:
    """The single call every Bridge v2 component makes before a dispatch or side effect."""
    return evaluate(feature, **kwargs).enabled
