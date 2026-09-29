"""Targeted fixtures for tools/bridge_v2_revocation.py (F0 durable revocation/freeze writer).

Authored under the 2026-09-29 operator no-runs directive: NOT executed by the author.
Independent review and CI run them. The only verifier used here is the TEST-ONLY
``_trusting`` function; production ships no verifier.

Mutation targets (each mutant must be killed by the named test):
  M1  deny transition writes updated_utc=now           -> test_deny_action_cannot_refresh_a_stale_state
  M2  accept duck-typed authorization objects           -> test_grant_refusals[lookalike]
  M3  ``verdict is not True`` weakened to ``not verdict`` -> test_grant_refusals[truthy]
  M4  missing verifier treated as allow                 -> test_grant_refusals[no_verifier]
  M5  from_version binding dropped / bool accepted      -> test_grant_is_single_use, test_grant_refusals[bool_version]
  M6  expiry or 15-minute lifetime check dropped        -> test_grant_refusals[expired], [too_long]
  M7  initialize overwrites without the exact sha256    -> test_initialize_never_replaces_silently
  M8  corrupt state replaced without a high-water mark  -> test_initialize_over_corrupt_needs_high_water
  M9  compare-and-swap re-read before replace skipped   -> test_state_changed_outside_the_lock_is_refused
  M10 temp file left behind on a failed publication     -> test_failed_replace_publishes_nothing_and_leaves_no_temp
  M11 lock skipped or unbounded                         -> test_lock_is_bounded_and_exclusive
  M12 deny transition accepts another policy binding    -> test_deny_transitions_refuse_missing_corrupt_or_rebound_state
  M13 initialize-replace drops existing revocations     -> test_initialize_replace_needs_exact_bytes_and_keeps_denials
  M14 version reused or not strictly increasing         -> test_expected_version_compare_and_swap, test_grant_is_single_use
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import bridge_v2_activation as act
from tools import bridge_v2_revocation as rev

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _policy(**overrides) -> dict:
    policy = json.loads((REPO / "configs" / "bridge_v2_activation.json").read_text(encoding="utf-8"))["policy"]
    policy["expires_utc"] = _stamp(NOW + timedelta(days=14))
    policy["revocation_max_age_seconds"] = 86400  # explicit freshness window (RCO2 F0-3)
    policy["features"]["F1"].update(enabled=True, stage=1)
    policy.update(overrides)
    return policy


def _env(tmp_path: Path, policy: dict | None = None) -> dict:
    policy = _policy() if policy is None else policy
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    return {"tmp": tmp_path, "root": runtime, "policy": policy, "digest": act.canonical_sha256(policy)}


@pytest.fixture
def env(tmp_path):
    return _env(tmp_path)


def _state_path(env) -> Path:
    return env["root"] / "bridge_v2" / "revocation.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _init(env, **kwargs) -> dict:
    return rev.initialize_frozen(env["root"], env["policy"], env["digest"], now=NOW, **kwargs)


def _auth(env, action, version, features=(), minutes=5, at=NOW, provenance="test-only"):
    return rev.OperatorAuthorization(action=action, policy_sha256=env["digest"], from_version=version,
                                     features=tuple(features), expires_utc=_stamp(at + timedelta(minutes=minutes)),
                                     provenance=provenance)


def _trusting(authorization) -> bool:
    """TEST-ONLY provenance verifier."""
    return authorization.provenance == "test-only"


def _unfrozen(env, at=NOW) -> dict:
    _init(env)
    return rev.unfreeze(env["root"], env["digest"], authorization=_auth(env, "unfreeze", 1, at=at),
                        verifier=_trusting, now=at)


def _decide(env, feature="F1", now=NOW) -> act.Decision:
    signature = {"schema": act.SIGNATURE_SCHEMA, "policy_sha256": env["digest"], "head": "a" * 40, "tree": "b" * 40,
                 "signed_utc": _stamp(NOW - timedelta(hours=1)), "expires_utc": _stamp(NOW + timedelta(days=14))}
    config = env["tmp"] / "activation.json"
    config.write_text(json.dumps({"policy": env["policy"], "signature": signature}), encoding="utf-8")
    return act.evaluate(feature, config_path=config, runtime_root=env["root"],
                        trusted_policy_sha256=env["digest"], now=now, environ={})


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

def test_initialize_frozen_creates_version_1_bound_frozen_and_evaluator_accepts_it(env):
    receipt = _init(env)
    assert receipt["action"] == "initialize_frozen" and receipt["version"] == 1 and receipt["frozen"] is True
    assert receipt["previous_version"] is None and receipt["previous_sha256"] is None
    state = rev.read_state(env["root"], env["digest"])
    assert state == {"schema": act.REVOCATION_SCHEMA, "version": 1, "policy_sha256": env["digest"],
                     "frozen": True, "revoked": [], "updated_utc": "2026-10-01T12:00:00.000000Z"}
    raw = _state_path(env).read_bytes()
    assert _sha(raw) == receipt["sha256"]
    assert raw == (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    assert act.load_revocation(env["root"], env["policy"], env["digest"], NOW) == state


def test_initialized_state_denies_until_an_authorized_unfreeze(env):
    _init(env)
    assert _decide(env).reason == "fleet freeze is active"
    _unfreeze_receipt = rev.unfreeze(env["root"], env["digest"], authorization=_auth(env, "unfreeze", 1),
                                     verifier=_trusting, now=NOW)
    assert _unfreeze_receipt["version"] == 2 and _unfreeze_receipt["frozen"] is False
    assert _decide(env).enabled is True
    rev.freeze(env["root"], env["digest"], now=NOW)
    assert _decide(env).reason == "fleet freeze is active"


def test_initialize_never_replaces_silently(env):
    _init(env)
    before = _state_path(env).read_bytes()
    with pytest.raises(rev.RevocationError, match="never replaces"):
        _init(env)
    with pytest.raises(rev.RevocationError, match="differs"):
        _init(env, replace_existing_sha256="0" * 64)
    assert _state_path(env).read_bytes() == before


def test_initialize_replace_needs_exact_bytes_and_keeps_denials(env):
    _unfrozen(env)
    rev.revoke(env["root"], env["digest"], ["F2"], now=NOW)
    old = _state_path(env).read_bytes()
    receipt = _init(env, replace_existing_sha256=_sha(old))
    assert receipt["version"] == 4 and receipt["frozen"] is True and receipt["revoked"] == ["F2"]
    assert Path(receipt["evidence_path"]).read_bytes() == old
    assert rev.read_state(env["root"], env["digest"])["revoked"] == ["F2"]


def test_initialize_over_corrupt_needs_high_water(env):
    (env["root"] / "bridge_v2").mkdir()
    _state_path(env).write_bytes(b"{not json")
    with pytest.raises(rev.RevocationError, match="high-water"):
        _init(env, replace_existing_sha256=_sha(b"{not json"))
    assert _state_path(env).read_bytes() == b"{not json"
    receipt = _init(env, replace_existing_sha256=_sha(b"{not json"), min_version=7)
    assert receipt["version"] == 8 and receipt["frozen"] is True and receipt["previous_version"] is None
    assert Path(receipt["evidence_path"]).read_bytes() == b"{not json"


def test_initialize_rebinds_another_policy_only_frozen(env):
    _unfrozen(env)
    other = _policy(policy_version=2)
    other_digest = act.canonical_sha256(other)
    current = _state_path(env).read_bytes()
    receipt = rev.initialize_frozen(env["root"], other, other_digest, now=NOW, replace_existing_sha256=_sha(current))
    assert receipt["version"] == 3 and receipt["frozen"] is True and receipt["policy_sha256"] == other_digest
    with pytest.raises(rev.StateInvalid):
        rev.read_state(env["root"], env["digest"])
    assert rev.read_state(env["root"], other_digest)["frozen"] is True


def test_initialize_validates_policy_digest_root_and_versions(env, tmp_path):
    with pytest.raises(rev.RevocationError, match="canonical digest"):
        rev.initialize_frozen(env["root"], env["policy"], "0" * 64, now=NOW)
    bad = dict(env["policy"], policy_version=0)
    with pytest.raises(act.ActivationError):
        rev.initialize_frozen(env["root"], bad, act.canonical_sha256(bad), now=NOW)
    with pytest.raises(rev.RevocationError, match="does not exist"):
        rev.initialize_frozen(tmp_path / "missing", env["policy"], env["digest"], now=NOW)
    with pytest.raises(rev.RevocationError, match="absolute"):
        rev.initialize_frozen("relative/root", env["policy"], env["digest"], now=NOW)
    with pytest.raises(rev.RevocationError):
        _init(env, min_version=True)
    with pytest.raises(rev.RevocationError, match="nothing to replace"):
        _init(env, replace_existing_sha256="0" * 64)
    assert not _state_path(env).exists()


# ---------------------------------------------------------------------------
# Deny-only transitions
# ---------------------------------------------------------------------------

def test_deny_transitions_refuse_missing_corrupt_or_rebound_state(env):
    with pytest.raises(rev.StateMissing):
        rev.freeze(env["root"], env["digest"], now=NOW)
    (env["root"] / "bridge_v2").mkdir()
    _state_path(env).write_bytes(b"[]")
    with pytest.raises(rev.StateInvalid):
        rev.freeze(env["root"], env["digest"], now=NOW)
    with pytest.raises(rev.StateInvalid):
        rev.revoke(env["root"], env["digest"], ["F1"], now=NOW)
    assert _state_path(env).read_bytes() == b"[]"
    _init(env, replace_existing_sha256=_sha(b"[]"), min_version=1)
    before = _state_path(env).read_bytes()
    with pytest.raises(rev.StateInvalid, match="another policy"):
        rev.freeze(env["root"], "1" * 64, now=NOW)
    assert _state_path(env).read_bytes() == before


def test_freeze_is_deny_only_and_carries_freshness_forward(env):
    _unfrozen(env)
    before = rev.read_state(env["root"], env["digest"])
    receipt = rev.freeze(env["root"], env["digest"], now=NOW + timedelta(hours=1))
    after = rev.read_state(env["root"], env["digest"])
    assert receipt["version"] == before["version"] + 1 and after["frozen"] is True
    assert after["updated_utc"] == before["updated_utc"]


def test_revoke_adds_sorted_unique_and_keeps_freshness(env):
    _unfrozen(env)
    before = rev.read_state(env["root"], env["digest"])
    rev.revoke(env["root"], env["digest"], ["F3", "F2", "F3"], now=NOW + timedelta(hours=1))
    after = rev.read_state(env["root"], env["digest"])
    assert after["revoked"] == ["F2", "F3"] and after["frozen"] is False
    assert after["updated_utc"] == before["updated_utc"]
    assert _decide(env, "F1").enabled is True
    for bad in (["F0"], [], "F1", ["F1", 2], ("F31",)):
        with pytest.raises(rev.RevocationError):
            rev.revoke(env["root"], env["digest"], bad, now=NOW)


def test_deny_action_cannot_refresh_a_stale_state(tmp_path):
    env = _env(tmp_path, _policy(revocation_max_age_seconds=3600))
    _unfrozen(env)
    assert _decide(env, now=NOW + timedelta(minutes=30)).enabled is True
    later = NOW + timedelta(hours=2)
    assert _decide(env, now=later).enabled is False
    rev.revoke(env["root"], env["digest"], ["F2"], now=later)
    rev.freeze(env["root"], env["digest"], now=later)
    state = rev.read_state(env["root"], env["digest"])
    assert state["updated_utc"] == "2026-10-01T12:00:00.000000Z"
    assert "stale" in _decide(env, now=later).reason


def test_expected_version_compare_and_swap(env):
    _init(env)
    with pytest.raises(rev.RevocationError, match="compare-and-swap"):
        rev.freeze(env["root"], env["digest"], now=NOW, expected_version=5)
    with pytest.raises(rev.RevocationError):
        rev.freeze(env["root"], env["digest"], now=NOW, expected_version=True)
    assert rev.freeze(env["root"], env["digest"], now=NOW, expected_version=1)["version"] == 2


# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------

def _raise(_authorization):
    raise RuntimeError("verifier failure")


def _bad_grant(env, case):
    good = _auth(env, "unfreeze", 1)
    return {
        "none": (None, _trusting),
        "string": ("operator", _trusting),
        "lookalike": (SimpleNamespace(**vars(good)), _trusting),
        "no_verifier": (good, None),
        "truthy": (good, lambda a: 1),
        "false": (good, lambda a: False),
        "raises": (good, _raise),
        "untrusted_provenance": (_auth(env, "unfreeze", 1, provenance="operator"), _trusting),
        "stale_version": (_auth(env, "unfreeze", 2), _trusting),
        "bool_version": (dataclasses.replace(good, from_version=True), _trusting),
        "other_policy": (dataclasses.replace(good, policy_sha256="0" * 64), _trusting),
        "other_action": (_auth(env, "reattest", 1), _trusting),
        "features": (_auth(env, "unfreeze", 1, features=("F1",)), _trusting),
        "list_features": (dataclasses.replace(good, features=[]), _trusting),
        "expired": (_auth(env, "unfreeze", 1, minutes=-1), _trusting),
        "too_long": (_auth(env, "unfreeze", 1, minutes=16), _trusting),
        "bad_expiry": (dataclasses.replace(good, expires_utc="2026-10-01T12:05:00+00:00"), _trusting),
    }[case]


@pytest.mark.parametrize("case", [
    "none", "string", "lookalike", "no_verifier", "truthy", "false", "raises", "untrusted_provenance",
    "stale_version", "bool_version", "other_policy", "other_action", "features", "list_features",
    "expired", "too_long", "bad_expiry",
])
def test_grant_refusals(env, case):
    _init(env)
    before = _state_path(env).read_bytes()
    authorization, verifier = _bad_grant(env, case)
    with pytest.raises(rev.AuthorizationRefused):
        rev.unfreeze(env["root"], env["digest"], authorization=authorization, verifier=verifier, now=NOW)
    assert _state_path(env).read_bytes() == before
    assert _decide(env).enabled is False


def test_grant_is_single_use(env):
    _init(env)
    authorization = _auth(env, "unfreeze", 1)
    assert rev.unfreeze(env["root"], env["digest"], authorization=authorization, verifier=_trusting,
                        now=NOW)["version"] == 2
    rev.freeze(env["root"], env["digest"], now=NOW)
    with pytest.raises(rev.AuthorizationRefused):
        rev.unfreeze(env["root"], env["digest"], authorization=authorization, verifier=_trusting, now=NOW)
    assert rev.read_state(env["root"], env["digest"])["frozen"] is True


def test_unrevoke_requires_exact_revoked_features(env):
    _unfrozen(env)
    rev.revoke(env["root"], env["digest"], ["F2", "F3"], now=NOW)
    receipt = rev.unrevoke(env["root"], env["digest"], ["F3"], authorization=_auth(env, "unrevoke", 3, ("F3",)),
                           verifier=_trusting, now=NOW)
    assert receipt["version"] == 4 and receipt["revoked"] == ["F2"]
    with pytest.raises(rev.RevocationError, match="not revoked"):
        rev.unrevoke(env["root"], env["digest"], ["F9"], authorization=_auth(env, "unrevoke", 4, ("F9",)),
                     verifier=_trusting, now=NOW)
    with pytest.raises(rev.AuthorizationRefused):
        rev.unrevoke(env["root"], env["digest"], ["F2"], authorization=_auth(env, "unrevoke", 4, ("F3",)),
                     verifier=_trusting, now=NOW)
    assert rev.read_state(env["root"], env["digest"])["revoked"] == ["F2"]


def test_reattest_refreshes_freshness_only_with_authorization(env):
    _unfrozen(env)
    at = NOW + timedelta(minutes=30)
    receipt = rev.reattest(env["root"], env["digest"], authorization=_auth(env, "reattest", 2, at=at),
                           verifier=_trusting, now=at)
    state = rev.read_state(env["root"], env["digest"])
    assert receipt["version"] == 3 and state["updated_utc"] == "2026-10-01T12:30:00.000000Z"
    assert state["frozen"] is False and state["revoked"] == []


# ---------------------------------------------------------------------------
# Lock and publication
# ---------------------------------------------------------------------------

def test_lock_is_bounded_and_exclusive(env):
    _init(env)
    lock = env["root"] / "bridge_v2" / "revocation.lock"
    with rev._locked(lock, 1.0):
        with pytest.raises(rev.RevocationError, match="busy"):
            rev.freeze(env["root"], env["digest"], now=NOW, lock_timeout=0.2)
    assert rev.freeze(env["root"], env["digest"], now=NOW, lock_timeout=0.2)["version"] == 2


@pytest.mark.parametrize("timeout", [0, -1, 61, True, float("nan"), "5"])
def test_lock_timeout_is_validated(env, timeout):
    _init(env)
    with pytest.raises(rev.RevocationError, match="lock timeout"):
        rev.freeze(env["root"], env["digest"], now=NOW, lock_timeout=timeout)


def test_failed_replace_publishes_nothing_and_leaves_no_temp(env, monkeypatch):
    _init(env)
    before = _state_path(env).read_bytes()

    def boom(_source, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(rev, "_replace", boom)
    with pytest.raises(OSError, match="simulated"):
        rev.freeze(env["root"], env["digest"], now=NOW)
    assert _state_path(env).read_bytes() == before
    assert list((env["root"] / "bridge_v2").glob("revocation.json.tmp-*")) == []


def test_transient_replace_permission_error_is_retried(env, monkeypatch):
    _init(env)
    real_replace = os.replace
    attempts = {"n": 0}

    def flaky(source, target):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise PermissionError("simulated sharing violation")
        return real_replace(source, target)

    monkeypatch.setattr(rev.os, "replace", flaky)
    monkeypatch.setattr(rev.time, "sleep", lambda _seconds: None)
    assert rev.freeze(env["root"], env["digest"], now=NOW)["version"] == 2
    assert attempts["n"] == 3


def test_state_changed_outside_the_lock_is_refused(env, monkeypatch):
    _init(env)
    state_path = _state_path(env)
    before = state_path.read_bytes()
    real = rev._read_raw_or_none
    seen = {"n": 0}

    def tampering(path, limit=rev.MAX_EVIDENCE_BYTES):
        data = real(path, limit)
        if Path(path) == state_path and data is not None:
            seen["n"] += 1
            if seen["n"] == 2:  # the compare-and-swap re-read just before os.replace
                return data + b" "
        return data

    monkeypatch.setattr(rev, "_read_raw_or_none", tampering)
    with pytest.raises(rev.RevocationError, match="outside the lock"):
        rev.freeze(env["root"], env["digest"], now=NOW)
    assert state_path.read_bytes() == before
    assert list((env["root"] / "bridge_v2").glob("revocation.json.tmp-*")) == []


def test_time_and_root_inputs_are_strict(env):
    _init(env)
    with pytest.raises(rev.RevocationError, match="timezone-aware"):
        rev.freeze(env["root"], env["digest"], now=NOW.replace(tzinfo=None))
    with pytest.raises(rev.RevocationError, match="absolute"):
        rev.freeze("relative/root", env["digest"], now=NOW)
    with pytest.raises(rev.RevocationError, match="64 lowercase hex"):
        rev.freeze(env["root"], env["digest"].upper(), now=NOW)


def test_module_ships_no_verifier_cli_or_production_entry_point():
    source = Path(rev.__file__).read_text(encoding="utf-8")
    assert "__main__" not in source and "argparse" not in source
    assert not any(name.lower().startswith(("default_verif", "trusted_verif")) for name in dir(rev))
