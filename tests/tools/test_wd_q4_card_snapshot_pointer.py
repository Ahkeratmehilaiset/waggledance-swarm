"""Synthetic C3 -> C2 -> staged resolver binding; no query or publication."""
import hashlib
import json

import pytest

from tools import wd_q4_public_pointer_resolver as resolver
from tests.tools.test_shared_card_checkpoint import staged


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def call(staged):
    pointer, checkpoint, directory = staged
    return dict(root=directory.parent, pointer_bytes=raw(pointer),
                expected_pointer_sha256=sha(raw(pointer)), enabled=True,
                card_decision_bytes=checkpoint["decision_bytes"],
                card_decision_sha256=checkpoint["decision_sha256"],
                **{key: checkpoint[key] for key in (
                    "policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256",
                    "card_policy_bytes", "source_path", "generation_path", "end_cursor")})


def resolve(call):
    return resolver.resolve_card_snapshot_pointer(**call)


def test_default_off_touches_no_input():
    class Untouchable:
        def __getattribute__(self, name):
            raise AssertionError("disabled input touched")
    with pytest.raises(resolver.PublicPointerRefused, match="^disabled$"):
        resolver.resolve_card_snapshot_pointer(Untouchable())


def test_actual_c3_c2_staged_binding_without_receipt_or_history(call):
    before = {str(p): p.read_bytes() for p in call["root"].rglob("*") if p.is_file()}
    binding = resolve(call)
    pointer = json.loads(call["pointer_bytes"])
    assert binding["status"] == "VALIDATED_STAGED"
    assert binding["source_mode"] == "card-snapshot-v1"
    assert binding["completeness"] == binding["freshness"] == "UNKNOWN"
    assert binding["identity"] == binding["publisher_authentication"] == "NOT_AUTHENTICATED"
    assert binding["pointer_sha256"] == call["expected_pointer_sha256"]
    assert binding["source_prefix"] == pointer["source_prefix"]
    assert binding["generation"] == pointer["generation"]
    assert binding["memory_root"] == str(call["root"] / pointer["generation"])
    assert binding["card_decision_sha256"] == call["card_decision_sha256"]
    assert binding["policy_sha256"] == call["policy_sha256"]
    assert binding["roster_sha256"] == call["roster_sha256"]
    assert binding["card_policy_sha256"] == sha(call["card_policy_bytes"])
    for name in ("memory", "descriptor", "projection"):
        assert binding[name + "_sha256"] == pointer["artifacts"][name + ".json"]
    assert not ({"receipt_sha256", "history", "previous", "watermark_sha256"} & binding.keys())
    assert before == {str(p): p.read_bytes() for p in call["root"].rglob("*") if p.is_file()}


@pytest.mark.parametrize("field", ["card_decision_sha256", "policy_sha256", "roster_sha256",
                                   "expected_pointer_sha256"])
def test_wrong_external_pin_refuses(call, field):
    call[field] = "0" * 64
    with pytest.raises(resolver.PublicPointerRefused, match="^resolution_refused$"):
        resolve(call)


@pytest.mark.parametrize("field", ["card_decision_bytes", "card_decision_sha256", "policy_bytes",
                                   "roster_bytes", "card_policy_bytes", "source_path",
                                   "generation_path", "end_cursor"])
def test_missing_c2_binding_refuses(call, field):
    call[field] = None
    with pytest.raises(resolver.PublicPointerRefused, match="^resolution_refused$"):
        resolve(call)


@pytest.mark.parametrize("kind", ["source", "generation", "projection", "cell", "pointer"])
def test_each_evidence_family_tamper_refuses(call, kind):
    from pathlib import Path
    pointer = json.loads(call["pointer_bytes"])
    directory = call["root"] / pointer["generation"]
    if kind == "pointer":
        pointer["previous"] = None
        call["pointer_bytes"] = raw(pointer)
        call["expected_pointer_sha256"] = sha(call["pointer_bytes"])
    else:
        if kind in ("source", "generation"):
            path = Path(call["source_path" if kind == "source" else "generation_path"])
        elif kind == "projection":
            path = directory / "projection.json"
        else:
            path = directory / next(name for name in pointer["artifacts"] if name.startswith("cells/"))
        original = path.read_bytes()
        path.write_bytes(b"!" + original[1:] if kind == "source" else original + b"synthetic tamper")
    with pytest.raises(resolver.PublicPointerRefused, match="^resolution_refused$"):
        resolve(call)


def test_no_fallback_to_legacy_history_verifier(call, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("staged route used v6 history")
    monkeypatch.setattr(resolver.checkpoint, "read_verified_pointer", forbidden)
    assert resolve(call)["status"] == "VALIDATED_STAGED"


def test_appended_source_suffix_is_outside_frozen_prefix(call):
    from pathlib import Path
    path = Path(call["source_path"])
    path.write_bytes(path.read_bytes() + b"synthetic later suffix\n")
    assert resolve(call)["status"] == "VALIDATED_STAGED"


def test_checkpoint_incomplete_never_becomes_binding(call, monkeypatch):
    from sandbox.mind.step2 import shared_card_checkpoint
    monkeypatch.setattr(shared_card_checkpoint, "validate_card_snapshot",
                        lambda **kwargs: {"status": "INCOMPLETE"})
    with pytest.raises(resolver.PublicPointerRefused, match="^resolution_refused$"):
        resolve(call)
