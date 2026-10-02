"""Caller-explicit F26 shadow input; no live capacity, signing or activation proof.

The positive route twin injects the existing SYNTHETIC worker fixture at the
capacity adapter port, while using the real reader, assembler, compose and router.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import pytest

import tools.wd_routing_capacity as capacity
import tools.wd_routing_reader as reader
from test_wd_routing_reader import NOW, _paths, _spy_opens, _write
from test_wd_task_router import NOW as ROUTER_NOW, fleet, policy, shadow_weights, task


def test_optional_shadow_document_reaches_real_assembler_and_compose_once(tmp_path, monkeypatch):
    weights = shadow_weights()
    paths = _paths(tmp_path)
    paths["shadow_weights"] = _write(tmp_path / "shadow.json", weights)
    raw = Path(paths["shadow_weights"]).read_bytes()
    opened = _spy_opens(monkeypatch)
    result = reader.read_routing_inputs(paths, NOW)
    assert "path_key_unsupported" not in result["reasons"]
    assert opened.count(paths["shadow_weights"]) == 1
    assert result["assembled"]["inputs"]["shadow_weights"] == weights
    record = result["reads"]["shadow_weights"]
    assert record["byte_sha256"] == hashlib.sha256(raw).hexdigest()
    assert record["size"] == len(raw)
    provenance = result["assembled"]["provenance"]["shadow_weights"]
    assert provenance["caller_byte_sha256"] == record["byte_sha256"]
    assert provenance["caller_path"] == paths["shadow_weights"]
    assert provenance["byte_digest_verified"] is False
    assert result["authority"] == "none" and result["execution_allowed"] is False
    assert result["assembled"]["inputs"]["workers"] == []


@pytest.mark.parametrize("value", [None, "omitted"])
def test_absent_shadow_has_no_read_or_error(tmp_path, monkeypatch, value):
    paths = _paths(tmp_path)
    if value is None:
        paths["shadow_weights"] = None
    opened = _spy_opens(monkeypatch)
    result = reader.read_routing_inputs(paths, NOW)
    assert result["assembled"]["inputs"]["shadow_weights"] is None
    assert result["reads"]["shadow_weights"] is None
    assert not any(reason.startswith("shadow_weights_") for reason in result["reasons"])
    assert len(opened) == len(set(opened))


@pytest.mark.parametrize(("raw", "refusal"), [
    (b'{"schema":', "not_strict_json"),
    (b'{"key":1,"key":2}', "not_strict_json"),
    (b'{"value":NaN}', "not_strict_json"),
    (b'{"value":1e400}', "not_strict_json"),
    (b'{"value":"\xff"}', "not_utf8"),
])
def test_bad_shadow_read_is_visible_without_changing_advice(tmp_path, raw, refusal):
    paths = _paths(tmp_path)
    baseline = reader.read_routing_inputs(paths, NOW)
    paths["shadow_weights"] = _write(tmp_path / "bad.json", raw=raw)
    result = reader.read_routing_inputs(paths, NOW)
    assert f"shadow_weights_unreadable:{refusal}" in result["reasons"]
    assert result["assembled"]["inputs"]["shadow_weights"] is None
    assert result["reads"]["shadow_weights"]["refusal"] == refusal
    assert result["composed"] == baseline["composed"]


def test_unlisted_environment_shadow_path_is_never_opened(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    secret = _write(tmp_path / "unlisted.json", shadow_weights())
    monkeypatch.setenv("WD_SHADOW_WEIGHTS", secret)
    opened = _spy_opens(monkeypatch)
    result = reader.read_routing_inputs(paths, NOW)
    assert secret not in opened
    assert result["assembled"]["inputs"]["shadow_weights"] is None


def test_valid_shadow_reorders_only_shadow_with_synthetic_capacity_port(tmp_path, monkeypatch):
    paths = _paths(tmp_path, task=task(), routing_policy=policy())
    # Explicit test-only port: checkpoints still prove neither profile nor quota.
    monkeypatch.setattr(capacity, "_with_capacity", lambda *args: (fleet(), {}))
    now = datetime.fromisoformat(ROUTER_NOW.replace("Z", "+00:00"))
    plain = reader.read_routing_inputs(paths, now)
    paths["shadow_weights"] = _write(tmp_path / "shadow.json", shadow_weights())
    shadowed = reader.read_routing_inputs(paths, now)
    before, after = plain["composed"]["advice"], shadowed["composed"]["advice"]
    assert before["verdict"] == after["verdict"] == "route"
    assert before["shadow"]["state"] == "absent"
    assert after["shadow"]["state"] == "derived"
    assert after["shadow"]["ranking"] == ["fable-5", "codex-tools-1"]
    for key in set(before) - {"shadow"}:
        assert after[key] == before[key], key
    assert after["authority"] == "none" and after["execution_allowed"] is False


@pytest.mark.parametrize("document", [[], "bad", 7, {"mode": "active", "authority": "lead"}])
def test_semantically_invalid_shadow_is_ignored_by_real_router(tmp_path, monkeypatch, document):
    paths = _paths(tmp_path, task=task(), routing_policy=policy())
    monkeypatch.setattr(capacity, "_with_capacity", lambda *args: (fleet(), {}))
    now = datetime.fromisoformat(ROUTER_NOW.replace("Z", "+00:00"))
    baseline = reader.read_routing_inputs(paths, now)["composed"]["advice"]
    paths["shadow_weights"] = _write(tmp_path / "invalid.json", document)
    result = reader.read_routing_inputs(paths, now)["composed"]["advice"]
    assert result["shadow"] == {"state": "ignored", "reason": "shadow_weights_invalid", "affects_advice": False}
    for key in set(baseline) - {"shadow"}:
        assert result[key] == baseline[key], key
