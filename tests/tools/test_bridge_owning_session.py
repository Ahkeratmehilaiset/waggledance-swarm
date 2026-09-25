"""Pure owning-session descriptor boundary tests."""
from __future__ import annotations

from copy import deepcopy
import builtins

import pytest

from tools.bridge_owning_session import (
    DESCRIPTOR_SCHEMA,
    LAUNCHER_EVIDENCE_SCHEMA,
    MAX_REPLAYED_DESCRIPTOR_IDS,
    descriptor_digest,
    validate_owning_session_descriptor,
)


NOW = "2026-09-25T10:00:00Z"
DESCRIPTOR_ID = "11111111-2222-4333-8444-555555555555"


def _descriptor() -> dict:
    return {
        "schema": DESCRIPTOR_SCHEMA,
        "descriptor_id": DESCRIPTOR_ID,
        "conversation_surface": "app_server_owned",
        "readiness_scope": "owner_bound_observation",
        "adapter_kind": "owning_session_adapter",
        "endpoint_uri": "wss://127.0.0.1:9443/owned-session",
        "issued_at_utc": "2026-09-25T09:59:00Z",
        "expires_at_utc": "2026-09-25T10:05:00Z",
        "binding": {
            "agent": "codex-tools-1",
            "agent_uuid": "7a8af68d-20bc-4598-9953-23c5dd98b102",
            "run_id": "wd-reboot-codex-tools-1-20260924T100627880Z-4244",
            "session_id": "wd-reboot-codex-tools-1-20260924T100627880Z-4244",
            "thread_id": "01a0a07b-ca98-71e1-90cb-d588435a2d8d",
            "native_pid": 23172,
            "native_process_start_utc": "2026-09-24T10:06:31.9297578Z",
            "launcher_pid": 4244,
            "launcher_process_start_utc": "2026-09-24T10:06:13Z",
            "generation": "1a36657fd026dec6b8551d4ebe357e714f4d05f9",
            "cli_sha256": "a" * 64,
        },
    }


def _evidence(descriptor: dict) -> dict:
    return {
        "schema": LAUNCHER_EVIDENCE_SCHEMA,
        "owner_verified": True,
        "descriptor_sha256": descriptor_digest(descriptor),
        "binding": deepcopy(descriptor["binding"]),
        "conversation_surface": descriptor["conversation_surface"],
        "readiness_scope": descriptor["readiness_scope"],
        "adapter_kind": descriptor["adapter_kind"],
        "endpoint_uri": descriptor["endpoint_uri"],
    }


def _validate(descriptor: dict, evidence: dict | None = None, **kwargs: object) -> dict:
    return validate_owning_session_descriptor(
        descriptor,
        _evidence(descriptor) if evidence is None else evidence,
        now_utc=NOW,
        **kwargs,
    )


def test_verified_descriptor_is_observation_only() -> None:
    result = _validate(_descriptor())

    assert result["valid"] is True
    assert result["observation_allowed"] is True
    assert result["control_allowed"] is False
    assert result["live_capability"] == "none"
    assert result["model_calls"] == 0
    assert result["io_operations"] == 0


@pytest.mark.parametrize("attestation", [True, False, "true", 1, None])
def test_descriptor_rejects_every_self_asserted_owner_field(attestation: object) -> None:
    descriptor = _descriptor()
    descriptor["owner_verified"] = attestation

    assert _validate(descriptor)["reason"] == "descriptor_self_attestation_forbidden"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("conversation_surface", "native_terminal", "native_terminal_forbidden"),
        ("readiness_scope", "native_cli_only", "native_cli_only_forbidden"),
        ("adapter_kind", "capacity_collector", "collector_descriptor_forbidden"),
        ("endpoint_uri", None, "missing_or_invalid_endpoint"),
        ("endpoint_uri", "stdio://", "missing_or_invalid_endpoint"),
        ("endpoint_uri", "wss://[", "missing_or_invalid_endpoint"),
        ("endpoint_uri", "wss://127.0.0.1:not-a-port", "missing_or_invalid_endpoint"),
        ("endpoint_uri", "wss://127.0.0.1:65536", "missing_or_invalid_endpoint"),
    ],
)
def test_terminal_collector_and_missing_endpoint_are_rejected(
    field: str, value: object, reason: str
) -> None:
    descriptor = _descriptor()
    descriptor[field] = value

    assert _validate(descriptor)["reason"] == reason


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("native_pid", True),
        ("launcher_pid", False),
        ("native_pid", None),
        ("launcher_pid", "4244"),
        ("native_process_start_utc", None),
        ("launcher_process_start_utc", "not-a-time"),
    ],
)
def test_pid_only_or_bool_as_int_binding_is_rejected(field: str, value: object) -> None:
    descriptor = _descriptor()
    descriptor["binding"][field] = value

    assert _validate(descriptor)["reason"] == f"invalid_binding:{field}"


@pytest.mark.parametrize(
    "field",
    [
        "agent",
        "run_id",
        "thread_id",
        "native_process_start_utc",
        "launcher_process_start_utc",
        "generation",
        "cli_sha256",
    ],
)
def test_independent_launcher_binding_must_match_every_critical_field(field: str) -> None:
    descriptor = _descriptor()
    evidence = _evidence(descriptor)
    evidence["binding"][field] = "mismatch"

    result = _validate(descriptor, evidence)

    assert result["valid"] is False
    assert result["reason"] == f"trusted_binding_mismatch:{field}"


def test_forged_launcher_bool_is_not_truthy_enough() -> None:
    descriptor = _descriptor()
    evidence = _evidence(descriptor)
    evidence["owner_verified"] = 1

    assert _validate(descriptor, evidence)["reason"] == "launcher_owner_not_verified"


def test_launcher_evidence_binds_the_exact_descriptor_digest() -> None:
    descriptor = _descriptor()
    evidence = _evidence(descriptor)
    descriptor["endpoint_uri"] = "wss://127.0.0.1:9443/replaced"

    assert _validate(descriptor, evidence)["reason"] == "trusted_descriptor_digest_mismatch"


def test_stale_and_replayed_descriptors_fail_closed() -> None:
    stale = _descriptor()
    stale["expires_at_utc"] = "2026-09-25T09:59:59Z"
    assert _validate(stale)["reason"] == "descriptor_stale_or_invalid_lifetime"

    descriptor = _descriptor()
    assert _validate(descriptor, replayed_descriptor_ids={DESCRIPTOR_ID})["reason"] == "descriptor_replayed"


def test_uuid_identity_is_canonical_lowercase_in_descriptor_and_replay_guard() -> None:
    canonical_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    descriptor = _descriptor()
    descriptor["descriptor_id"] = canonical_id.upper()
    assert _validate(descriptor)["reason"] == "invalid_descriptor_id"

    descriptor = _descriptor()
    descriptor["descriptor_id"] = canonical_id
    assert _validate(descriptor, replayed_descriptor_ids={canonical_id.upper()})[
        "reason"
    ] == "invalid_replay_guard"


def test_replay_guard_bounds_an_unbounded_iterable_before_materialising_it() -> None:
    class RepeatedDescriptorIds:
        def __init__(self) -> None:
            self.seen = 0

        def __iter__(self) -> "RepeatedDescriptorIds":
            return self

        def __next__(self) -> str:
            self.seen += 1
            return DESCRIPTOR_ID

    replayed = RepeatedDescriptorIds()
    result = _validate(_descriptor(), replayed_descriptor_ids=replayed)

    assert result["reason"] == "invalid_replay_guard"
    assert replayed.seen == MAX_REPLAYED_DESCRIPTOR_IDS + 1


def test_missing_null_or_non_object_inputs_fail_closed() -> None:
    assert validate_owning_session_descriptor(None, None, now_utc=NOW)["reason"] == "descriptor_not_object"
    assert validate_owning_session_descriptor(_descriptor(), None, now_utc=NOW)["reason"] == "launcher_evidence_not_object"
    assert validate_owning_session_descriptor(_descriptor(), _evidence(_descriptor()), now_utc=None)["reason"] == "invalid_now_utc"


def test_ten_thousand_idle_checks_have_no_file_or_model_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    descriptor = _descriptor()
    evidence = _evidence(descriptor)

    def forbidden_open(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("pure descriptor validation must not open files")

    monkeypatch.setattr(builtins, "open", forbidden_open)
    for _ in range(10_000):
        result = _validate(descriptor, evidence)
        assert result["valid"] is True
        assert result["io_operations"] == 0
        assert result["model_calls"] == 0
        assert result["control_allowed"] is False
