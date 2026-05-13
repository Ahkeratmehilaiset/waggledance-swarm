# SPDX-License-Identifier: BUSL-1.1
"""Tests for CredentialVault interface + NoOpVault + InMemoryVault.

OSKeyringVault is exercised via mock-keyring tests; real OS keyring
integration is operator-driven (requires `pip install keyring`).
"""
from __future__ import annotations

import pickle
import pytest
from unittest.mock import patch, MagicMock

from waggledance.core.v3_13_0.credential_vault import (
    CredentialRef,
    CredentialMaterial,
    VaultMetadata,
    StoreResult,
    RotateResult,
    RevokeResult,
    CredentialRefSummary,
    NoOpVault,
    InMemoryVault,
    OSKeyringVault,
)


# ============================================================================
# CredentialRef
# ============================================================================


class TestCredentialRef:

    def test_parse_valid_uri(self):
        ref = CredentialRef.parse(
            "vault://os_keyring/profile_42/factory_anchor/pdam_session"
        )
        assert ref.impl == "os_keyring"
        assert ref.tenant == "profile_42"
        assert ref.scope == "factory_anchor"
        assert ref.name == "pdam_session"

    def test_parse_rejects_missing_scheme(self):
        with pytest.raises(ValueError):
            CredentialRef.parse("os_keyring/profile_42/scope/name")

    def test_parse_rejects_uppercase_in_tenant(self):
        with pytest.raises(ValueError):
            CredentialRef.parse(
                "vault://os_keyring/Profile_42/scope/name"
            )

    def test_parse_rejects_pii_looking_email(self):
        # Email contains @ which is not in the allowed punctuation set.
        # This is the schema-level guard against accidentally putting
        # PII in tenant fields per Codex RCO edit #10.
        with pytest.raises(ValueError):
            CredentialRef.parse(
                "vault://os_keyring/jani@example.com/scope/name"
            )

    def test_ref_is_frozen(self):
        ref = CredentialRef.parse(
            "vault://os_keyring/profile_42/scope/name"
        )
        with pytest.raises(Exception):
            ref.impl = "hijacked"  # type: ignore


# ============================================================================
# CredentialMaterial -- the redacted wrapper
# ============================================================================


class TestCredentialMaterial:

    def test_repr_redacted(self):
        mat = CredentialMaterial(b"super-secret-token-xyz")
        assert "super-secret" not in repr(mat)
        assert "redacted" in repr(mat).lower()

    def test_str_redacted(self):
        mat = CredentialMaterial(b"super-secret-token-xyz")
        assert "super-secret" not in str(mat)

    def test_pickle_refused(self):
        mat = CredentialMaterial(b"super-secret-token-xyz")
        with pytest.raises(TypeError):
            pickle.dumps(mat)

    def test_deepcopy_refused(self):
        import copy
        mat = CredentialMaterial(b"super-secret-token-xyz")
        with pytest.raises(TypeError):
            copy.deepcopy(mat)

    def test_shallow_copy_refused(self):
        import copy
        mat = CredentialMaterial(b"super-secret-token-xyz")
        with pytest.raises(TypeError):
            copy.copy(mat)

    def test_reveal_requires_purpose(self):
        mat = CredentialMaterial(b"super-secret-token-xyz")
        with pytest.raises(ValueError):
            mat.reveal(purpose="")

    def test_reveal_returns_raw_bytes(self):
        mat = CredentialMaterial(b"super-secret-token-xyz")
        assert mat.reveal(purpose="testing") == b"super-secret-token-xyz"

    def test_reveal_emits_audit_event(self):
        events = []
        mat = CredentialMaterial(
            b"super-secret-token-xyz",
            ref=CredentialRef.parse(
                "vault://os_keyring/profile_42/scope/name"
            ),
        )
        mat.reveal(purpose="testing audit",
                    audit_emit=lambda env: events.append(env))
        assert len(events) == 1
        assert events[0]["event_type"] == "auth.material_revealed"
        assert events[0]["purpose"] == "testing audit"
        assert events[0]["ref"] == \
            "vault://os_keyring/profile_42/scope/name"

    def test_equality_disabled(self):
        mat_a = CredentialMaterial(b"same-bytes")
        mat_b = CredentialMaterial(b"same-bytes")
        # equality returns NotImplemented; Python falls back to identity
        assert (mat_a == mat_b) is False

    def test_construct_rejects_non_bytes(self):
        with pytest.raises(TypeError):
            CredentialMaterial("string-not-bytes")  # type: ignore


# ============================================================================
# NoOpVault -- sentinel for isolation contexts
# ============================================================================


class TestNoOpVault:

    def test_has_returns_false(self):
        vault = NoOpVault()
        ref = CredentialRef.parse("vault://os_keyring/p/s/n")
        assert vault.has(ref) is False

    def test_get_raises_loudly(self):
        vault = NoOpVault()
        ref = CredentialRef.parse("vault://os_keyring/p/s/n")
        with pytest.raises(NotImplementedError) as exc:
            vault.get(ref, purpose="test")
        assert "NoOpVault refuses" in str(exc.value)

    def test_store_raises(self):
        vault = NoOpVault()
        ref = CredentialRef.parse("vault://os_keyring/p/s/n")
        with pytest.raises(NotImplementedError):
            vault.store(ref, b"value", VaultMetadata())

    def test_list_refs_empty(self):
        vault = NoOpVault()
        assert vault.list_refs() == []


# ============================================================================
# InMemoryVault -- test/ephemeral fake
# ============================================================================


class TestInMemoryVault:

    def _ref(self, name: str = "token") -> CredentialRef:
        return CredentialRef.parse(
            f"vault://os_keyring/profile_42/test_scope/{name}"
        )

    def test_store_then_has(self):
        vault = InMemoryVault()
        ref = self._ref()
        vault.store(ref, b"my-secret", VaultMetadata(provider="test"))
        assert vault.has(ref) is True

    def test_store_then_get_returns_material(self):
        events = []
        vault = InMemoryVault(audit_emit=lambda env: events.append(env))
        ref = self._ref()
        vault.store(ref, b"my-secret", VaultMetadata())
        mat = vault.get(ref, purpose="testing")
        assert mat.reveal(purpose="testing inner") == b"my-secret"
        # store emits 1 event, get emits 1, reveal-test does not emit
        # (because we didn't pass audit_emit to reveal)
        event_types = [e["event_type"] for e in events]
        assert "auth.credential_stored" in event_types
        assert "auth.credential_retrieved" in event_types

    def test_get_requires_purpose(self):
        vault = InMemoryVault()
        ref = self._ref()
        vault.store(ref, b"my-secret", VaultMetadata())
        with pytest.raises(ValueError):
            vault.get(ref, purpose="")

    def test_get_missing_raises_keyerror(self):
        vault = InMemoryVault()
        ref = self._ref()
        with pytest.raises(KeyError):
            vault.get(ref, purpose="testing")

    def test_rotate_preserves_metadata_and_records_prior_hash(self):
        events = []
        vault = InMemoryVault(audit_emit=lambda env: events.append(env))
        ref = self._ref()
        vault.store(ref, b"v1", VaultMetadata(provider="test"))
        result = vault.rotate(ref, b"v2")
        assert result.success is True
        assert result.prior_value_hash is not None
        # New value retrievable
        mat = vault.get(ref, purpose="post-rotate")
        assert mat.reveal(purpose="check") == b"v2"
        # Audit chain emits rotated event
        assert any(e["event_type"] == "auth.credential_rotated"
                    for e in events)

    def test_revoke_blocks_subsequent_get(self):
        vault = InMemoryVault()
        ref = self._ref()
        vault.store(ref, b"v1", VaultMetadata())
        revoke_result = vault.revoke(ref, "no longer needed")
        assert revoke_result.success is True
        with pytest.raises(PermissionError):
            vault.get(ref, purpose="post-revoke")

    def test_revoke_requires_reason(self):
        vault = InMemoryVault()
        ref = self._ref()
        vault.store(ref, b"v1", VaultMetadata())
        revoke_result = vault.revoke(ref, "")
        assert revoke_result.success is False

    def test_list_refs_excludes_material(self):
        vault = InMemoryVault()
        ref = self._ref()
        vault.store(ref, b"my-secret", VaultMetadata(provider="test"))
        summaries = vault.list_refs()
        assert len(summaries) == 1
        summary = summaries[0]
        # The summary type has no material field; verify via repr/dict
        assert "my-secret" not in repr(summary)
        assert summary.ref.uri == ref.uri
        assert summary.metadata.provider == "test"
        assert summary.status == "active"

    def test_audit_events_never_contain_material(self):
        events = []
        vault = InMemoryVault(audit_emit=lambda env: events.append(env))
        ref = self._ref()
        vault.store(ref, b"super-secret-material", VaultMetadata())
        vault.get(ref, purpose="testing")
        vault.rotate(ref, b"new-super-secret")
        vault.revoke(ref, "rotation complete")
        # Every event flattened to JSON should not contain "super-secret"
        import json
        for env in events:
            blob = json.dumps(env, sort_keys=True, default=str)
            assert "super-secret" not in blob
            assert "new-super-secret" not in blob


# ============================================================================
# OSKeyringVault -- with mocked keyring module
# ============================================================================


class TestOSKeyringVault:

    def _ref(self) -> CredentialRef:
        return CredentialRef.parse(
            "vault://os_keyring/profile_42/test_scope/token"
        )

    def test_get_raises_if_keyring_not_installed(self):
        vault = OSKeyringVault()
        ref = self._ref()
        # Patch import to simulate missing keyring
        with patch.object(OSKeyringVault, "_keyring",
                            side_effect=RuntimeError(
                                "OSKeyringVault requires the 'keyring' package."
                            )):
            with pytest.raises(RuntimeError) as exc:
                vault.get(ref, purpose="testing")
            assert "keyring" in str(exc.value).lower()

    def test_get_purpose_required(self):
        vault = OSKeyringVault()
        ref = self._ref()
        with pytest.raises(ValueError):
            vault.get(ref, purpose="")

    def test_service_name_format(self):
        vault = OSKeyringVault()
        ref = self._ref()
        svc = vault._service(ref)
        assert svc == "waggledance.v3_13_0.os_keyring.profile_42.test_scope"

    def test_store_get_round_trip_with_mock_keyring(self):
        """Mocked keyring round-trip; no real OS keyring touched."""
        events = []
        vault = OSKeyringVault(audit_emit=lambda env: events.append(env))
        ref = self._ref()

        mock_kr = MagicMock()
        storage = {}

        def mock_set(svc, name, value):
            storage[(svc, name)] = value

        def mock_get(svc, name):
            return storage.get((svc, name))

        def mock_delete(svc, name):
            storage.pop((svc, name), None)

        mock_kr.set_password = mock_set
        mock_kr.get_password = mock_get
        mock_kr.delete_password = mock_delete

        with patch.object(OSKeyringVault, "_keyring",
                            return_value=mock_kr):
            vault.store(ref, b"keyring-value", VaultMetadata(provider="test"))
            mat = vault.get(ref, purpose="testing")
            assert mat.reveal(purpose="check") == b"keyring-value"
            # Material not in audit events
            for env in events:
                import json
                assert "keyring-value" not in json.dumps(env, default=str)

    def test_revoke_marks_status_and_blocks_get(self):
        vault = OSKeyringVault()
        ref = self._ref()
        mock_kr = MagicMock()
        storage = {}
        mock_kr.set_password = lambda s, n, v: storage.__setitem__((s, n), v)
        mock_kr.get_password = lambda s, n: storage.get((s, n))
        mock_kr.delete_password = lambda s, n: storage.pop((s, n), None)

        with patch.object(OSKeyringVault, "_keyring",
                            return_value=mock_kr):
            vault.store(ref, b"v1", VaultMetadata())
            revoke = vault.revoke(ref, "rotation complete")
            assert revoke.success is True
            with pytest.raises(PermissionError):
                vault.get(ref, purpose="post-revoke")


# ============================================================================
# Cross-cutting: no material in any logging path
# ============================================================================


class TestNoMaterialLeak:

    def test_material_not_in_pickle(self):
        """ANTI-004 contract: material does not survive pickle."""
        mat = CredentialMaterial(b"sensitive-bytes")
        with pytest.raises(TypeError):
            pickle.dumps(mat)

    def test_summary_does_not_carry_material(self):
        """list_refs() summaries are material-free."""
        vault = InMemoryVault()
        ref = CredentialRef.parse("vault://os_keyring/p/s/n")
        vault.store(ref, b"sensitive-bytes", VaultMetadata())
        summaries = vault.list_refs()
        # The dataclass has no material field; verify shape.
        s = summaries[0]
        # Ensure no attribute carries the raw value
        for attr_name in vars(s):
            attr_val = getattr(s, attr_name)
            assert b"sensitive-bytes" not in repr(attr_val).encode("utf-8")
