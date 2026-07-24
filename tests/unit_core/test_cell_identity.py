# SPDX-License-Identifier: BUSL-1.1
"""Adversarial matrix for CellIdentityV1 (W2A pure contract).

Every forgery/malformation must fail with a named reason; the honest path must
pass (liveness). Optional-field discipline: absent key, explicit null, and
wrong type are THREE distinct cases per field and all three are exercised.
"""

from __future__ import annotations

import pytest

from waggledance.core.cell_identity import (
    IDENTITY_KEYS,
    CellIdentityError,
    CellIdentityV1,
    build_cell_identity,
    derive_cell_id,
    verify_cell_identity,
)

_PUBKEY = "sha256:" + "a" * 64
_GENESIS = "sha256:" + "b" * 64
_CREATED = "2026-07-24T06:45:00Z"


def _identity():
    return build_cell_identity(
        pubkey_digest=_PUBKEY,
        genesis_material_digest=_GENESIS,
        created_at_utc=_CREATED,
    )


def _mapping():
    return _identity().to_mapping()


def test_honest_identity_builds_and_verifies():
    ok, reason = verify_cell_identity(_mapping())
    assert ok is True
    assert reason is None


def test_cell_id_is_deterministic_and_input_sensitive():
    assert _identity().cell_id == _identity().cell_id
    other = build_cell_identity(
        pubkey_digest=_PUBKEY,
        genesis_material_digest="sha256:" + "c" * 64,
        created_at_utc=_CREATED,
    )
    assert other.cell_id != _identity().cell_id


def test_rebuild_from_same_genesis_facts_reclaims_same_identity():
    """The hex rebuild property: same facts anywhere -> same cell_id."""
    first = _identity()
    rebuilt = build_cell_identity(
        pubkey_digest=first.pubkey_digest,
        genesis_material_digest=first.genesis_material_digest,
        created_at_utc=first.created_at_utc,
    )
    assert rebuilt == first


def test_forged_cell_id_rejected_at_construction():
    with pytest.raises(CellIdentityError, match="derived identity digest"):
        CellIdentityV1(
            cell_id="sha256:" + "f" * 64,
            pubkey_digest=_PUBKEY,
            genesis_material_digest=_GENESIS,
            created_at_utc=_CREATED,
        )


def test_forged_cell_id_rejected_by_verifier():
    forged = _mapping()
    forged["cell_id"] = "sha256:" + "f" * 64
    ok, reason = verify_cell_identity(forged)
    assert ok is False
    assert reason == "cell_id_mismatch"


def test_schema_version_pinned():
    with pytest.raises(CellIdentityError, match="schema_version"):
        CellIdentityV1(
            cell_id=_identity().cell_id,
            pubkey_digest=_PUBKEY,
            genesis_material_digest=_GENESIS,
            created_at_utc=_CREATED,
            schema_version="wd.cell_identity.v2",
        )
    wrong = _mapping()
    wrong["schema_version"] = "wd.cell_identity.v2"
    assert verify_cell_identity(wrong) == (False, "schema_version")


def test_smuggled_extra_key_rejected():
    smuggled = _mapping()
    smuggled["grants_runtime_authority"] = True
    assert verify_cell_identity(smuggled) == (False, "keyset")


@pytest.mark.parametrize("key", sorted(IDENTITY_KEYS))
def test_absent_key_rejected(key):
    broken = _mapping()
    del broken[key]
    ok, reason = verify_cell_identity(broken)
    assert ok is False
    assert reason == "keyset"


@pytest.mark.parametrize("key", sorted(IDENTITY_KEYS))
def test_present_null_rejected(key):
    """Explicit null is PRESENT-but-invalid, never conflated with absent."""
    broken = _mapping()
    broken[key] = None
    ok, reason = verify_cell_identity(broken)
    assert ok is False
    assert reason != "keyset"  # distinct clause from the absent case


@pytest.mark.parametrize("key", sorted(IDENTITY_KEYS))
def test_present_wrong_type_rejected(key):
    broken = _mapping()
    broken[key] = 12345
    ok, _ = verify_cell_identity(broken)
    assert ok is False


@pytest.mark.parametrize(
    "value",
    [
        "sha256:" + "A" * 64,  # uppercase hex refused
        "sha256:" + "a" * 63,
        "md5:" + "a" * 64,
        "a" * 64,
        "",
    ],
)
def test_digest_shape_enforced(value):
    with pytest.raises(CellIdentityError):
        derive_cell_id(
            pubkey_digest=value,
            genesis_material_digest=_GENESIS,
            created_at_utc=_CREATED,
        )


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-24T06:45:00",       # missing Z
        "2026-07-24 06:45:00Z",      # space separator
        "2026-07-24T06:45:00+00:00", # offset form refused (canonical Z only)
        "not-a-time",
        "",
    ],
)
def test_created_at_shape_enforced(value):
    with pytest.raises(CellIdentityError, match="created_at_utc"):
        derive_cell_id(
            pubkey_digest=_PUBKEY,
            genesis_material_digest=_GENESIS,
            created_at_utc=value,
        )


@pytest.mark.parametrize(
    "value",
    [
        "2026-99-99T99:99:99Z",  # the probe case: shape-valid, impossible
        "2026-13-01T00:00:00Z",  # month 13
        "2026-02-30T00:00:00Z",  # Feb 30
        "2026-02-29T00:00:00Z",  # 2026 is not a leap year
        "2026-07-24T24:00:00Z",  # hour 24
        "2026-07-24T06:60:00Z",  # minute 60
    ],
)
def test_impossible_calendar_instants_rejected(value):
    """Regex pins shape; the parse must pin reality (lead's runtime probe)."""
    with pytest.raises(CellIdentityError, match="calendar"):
        derive_cell_id(
            pubkey_digest=_PUBKEY,
            genesis_material_digest=_GENESIS,
            created_at_utc=value,
        )
    broken = _mapping()
    broken["created_at_utc"] = value
    assert verify_cell_identity(broken) == (False, "created_at_utc")


def test_real_leap_day_and_fractional_seconds_accepted():
    for created in (
        "2024-02-29T23:59:59Z",
        "2026-07-24T06:45:00.123456789Z",
        "2026-07-24T06:45:00.1Z",
        "2026-07-24T06:45:00.102Z",  # zero INSIDE the fraction is fine
    ):
        identity = build_cell_identity(
            pubkey_digest=_PUBKEY,
            genesis_material_digest=_GENESIS,
            created_at_utc=created,
        )
        assert verify_cell_identity(identity.to_mapping()) == (True, None)


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-24T06:45:00.0Z",    # same instant as 00Z
        "2026-07-24T06:45:00.00Z",
        "2026-07-24T06:45:00.100Z",  # same instant as .1Z
        "2026-07-24T06:45:00.120Z",
    ],
)
def test_noncanonical_fraction_spellings_rejected(value):
    """One instant, one lexeme: trailing-zero fractions would mint DIFFERENT
    cell_ids for the SAME UTC instant (lead's second probe)."""
    with pytest.raises(CellIdentityError, match="trailing zeros"):
        derive_cell_id(
            pubkey_digest=_PUBKEY,
            genesis_material_digest=_GENESIS,
            created_at_utc=value,
        )
    broken = _mapping()
    broken["created_at_utc"] = value
    assert verify_cell_identity(broken) == (False, "created_at_utc")


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-24T06:45:0٠Z",       # Arabic-Indic zero in seconds
        "2026-07-24T06:45:00.١Z",     # Arabic-Indic one in fraction
        "2026-07-24T06:45:00.０Z",     # fullwidth zero in fraction
        "２026-07-24T06:45:00Z",       # fullwidth digit in year
        "2026-07-24T06:45:00.٩٨Z",  # Arabic-Indic in fraction
    ],
)
def test_unicode_decimal_digits_rejected(value):
    """Python's \\d matches Unicode decimals; the ASCII-only regex must reject
    them on BOTH paths so a non-canonical spelling cannot mint an identity
    (lead's third probe)."""
    with pytest.raises(CellIdentityError, match="Z suffix"):
        derive_cell_id(
            pubkey_digest=_PUBKEY,
            genesis_material_digest=_GENESIS,
            created_at_utc=value,
        )
    broken = _mapping()
    broken["created_at_utc"] = value
    assert verify_cell_identity(broken) == (False, "created_at_utc")


def test_one_instant_cannot_mint_two_identities():
    """The canonical spelling passes; every alternate spelling of the same
    instant is rejected, so no instant has two derivable cell_ids."""
    canonical = derive_cell_id(
        pubkey_digest=_PUBKEY,
        genesis_material_digest=_GENESIS,
        created_at_utc="2026-07-24T06:45:00Z",
    )
    assert canonical
    for alias in ("2026-07-24T06:45:00.0Z", "2026-07-24T06:45:00.000Z"):
        with pytest.raises(CellIdentityError):
            derive_cell_id(
                pubkey_digest=_PUBKEY,
                genesis_material_digest=_GENESIS,
                created_at_utc=alias,
            )


class _EqAnyStr(str):
    """A str subclass that compares equal to anything -- the classic
    permissive-__eq__ forgery. Exact-type checks must reject it."""

    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    def __hash__(self):
        return 0


def test_eq_any_str_forged_fields_rejected_construct_and_verify():
    good = _identity()
    forged_id = _EqAnyStr("sha256:" + "0" * 64)
    # Constructor must reject a permissive-__eq__ cell_id (no self-cert).
    with pytest.raises(CellIdentityError):
        CellIdentityV1(
            cell_id=forged_id,
            pubkey_digest=_PUBKEY,
            genesis_material_digest=_GENESIS,
            created_at_utc=_CREATED,
        )
    # Verifier must reject it in every digest-bound field + schema.
    for key in ("cell_id", "pubkey_digest", "genesis_material_digest"):
        broken = good.to_mapping()
        broken[key] = _EqAnyStr(broken[key])
        ok, _ = verify_cell_identity(broken)
        assert ok is False, key
    broken = good.to_mapping()
    broken["schema_version"] = _EqAnyStr("anything")
    assert verify_cell_identity(broken) == (False, "schema_version")


def test_dict_subclass_flip_rejected_without_invoking_protocol():
    """A dict SUBCLASS with an overridden __getitem__ (that would flip cell_id
    between reads) is rejected immediately as not_mapping by the exact-dict
    gate -- the verifier never calls the overridden protocol at all. This is
    the totality guarantee: an attacker-controlled method cannot flap, raise,
    or fail to return, because it is never invoked."""
    good = _identity().to_mapping()
    forged = "sha256:" + "f" * 64
    invoked = {"getitem": False}

    class _FlipCellId(dict):
        def __getitem__(self, key):
            invoked["getitem"] = True
            if key == "cell_id":
                return forged
            return super().__getitem__(key)

    assert verify_cell_identity(_FlipCellId(good)) == (False, "not_mapping")
    assert invoked["getitem"] is False  # overridden protocol never touched


def test_alias_key_and_mapping_protocol_failures_fail_closed():
    """The raw mapping boundary is total. An alias/subclass KEY inside a plain
    dict rejects on the exact-str-key check; a non-dict Mapping and a dict
    subclass reject on the exact-dict gate BEFORE any of their (hostile) keys()/
    getitem is invoked. All four cases return not_mapping."""
    from collections.abc import Mapping as _Map

    good = _identity().to_mapping()

    # (a) str-subclass key that impersonates a canonical key by hash/eq. The
    # container is a plain dict, so the gate admits it, then the exact-str-key
    # walk rejects the EqAnyStr key.
    alias = {**good}
    del alias["cell_id"]
    alias[_EqAnyStr("cell_id")] = good["cell_id"]
    assert verify_cell_identity(alias) == (False, "not_mapping")

    # (b) a non-dict Mapping with a hostile keys(): rejected by the exact-dict
    # gate; keys() is NEVER called (totality).
    keys_called = {"hit": False}

    class _BadKeys(_Map):
        def __iter__(self):
            return iter(())

        def __len__(self):
            return 0

        def __getitem__(self, k):
            raise KeyError(k)

        def keys(self):
            keys_called["hit"] = True
            raise RuntimeError("hostile keys()")

    assert verify_cell_identity(_BadKeys()) == (False, "not_mapping")
    assert keys_called["hit"] is False

    # (c) a dict subclass with a hostile getitem: rejected by the exact-dict
    # gate; getitem is NEVER called.
    get_called = {"hit": False}

    class _BadGet(dict):
        def __getitem__(self, k):
            get_called["hit"] = True
            raise RuntimeError("hostile getitem")

    assert verify_cell_identity(_BadGet(good)) == (False, "not_mapping")
    assert get_called["hit"] is False

    # (d) a non-dict Mapping declaring a duplicate key: rejected by the gate;
    # its custom iteration is never consulted.
    class _DupKeys(_Map):
        def __init__(self, base):
            self._base = dict(base)

        def __iter__(self):
            return iter(list(self._base) + ["cell_id"])

        def keys(self):
            return list(self._base) + ["cell_id"]

        def __len__(self):
            return len(self._base) + 1

        def __getitem__(self, k):
            return self._base[k]

    assert verify_cell_identity(_DupKeys(good)) == (False, "not_mapping")


def test_non_mapping_inputs_fail_closed():
    for value in (None, "identity", 7, ["x"], object()):
        ok, reason = verify_cell_identity(value)
        assert ok is False
        assert reason == "not_mapping"


def test_custom_mapping_never_returns_is_rejected_without_invoking_it():
    """The lead's SlowKeys totality evidence: a custom Mapping whose every
    protocol method would abort (stand-in for 'never returns') is rejected by
    the exact-dict gate BEFORE any method runs, so verify_cell_identity is
    total and cannot hang on an attacker-declared shape."""
    from collections.abc import Mapping as _Map

    class _SlowKeys(_Map):
        def __iter__(self):
            raise AssertionError("iter must never be called")

        def __len__(self):
            raise AssertionError("len must never be called")

        def __getitem__(self, k):
            raise AssertionError("getitem must never be called")

        def keys(self):
            raise AssertionError("keys must never be called")

    assert verify_cell_identity(_SlowKeys()) == (False, "not_mapping")


def test_wire_dict_returns_an_isolated_builtin_copy():
    """The exact-dict boundary copies once: the returned snapshot equals the
    input but is a distinct object, and mutating either side afterwards does not
    affect the other -- so validation and use read a private, stable value."""
    from waggledance.core.cell_identity import _require_wire_dict

    src = {"a": 1, "b": 2}
    snap = _require_wire_dict(src)
    assert snap == src and snap is not src
    snap["a"] = 999
    assert src["a"] == 1  # mutating the copy never reaches the input
    src["c"] = 3
    assert "c" not in snap  # mutating the input never reaches the copy


def test_record_is_immutable():
    identity = _identity()
    with pytest.raises(Exception):
        identity.cell_id = "sha256:" + "0" * 64  # type: ignore[misc]


def test_no_authority_fields_exist():
    """The keyset IS the authority ceiling: nothing grant-like is present."""
    assert not {
        key
        for key in IDENTITY_KEYS
        if "grant" in key or "authority" in key or "budget" in key
    }


def test_determinism_over_many_derivations():
    first = derive_cell_id(
        pubkey_digest=_PUBKEY,
        genesis_material_digest=_GENESIS,
        created_at_utc=_CREATED,
    )
    assert all(
        derive_cell_id(
            pubkey_digest=_PUBKEY,
            genesis_material_digest=_GENESIS,
            created_at_utc=_CREATED,
        )
        == first
        for _ in range(100)
    )
