"""Synthetic lane link; no installed bundle, model or real bridge is touched."""
import hashlib
import io
import json

import pytest

from tools import wd_q4_lane_link as link


LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")


class NoRead:
    def read(self, *args):
        raise AssertionError("query was read before authorization")


@pytest.fixture
def plan(tmp_path):
    root = tmp_path / ("a" * 40)
    root.mkdir()
    wrapper = root / "Invoke-WdQ4Query.ps1"
    wrapper.write_bytes(b"synthetic wrapper; never executed")
    wrapper_sha = hashlib.sha256(wrapper.read_bytes()).hexdigest()
    manifest = {"schema_version": 1, "source_commit": "a" * 40,
                "files": {"Invoke-WdQ4Query.ps1": wrapper_sha}}
    raw = json.dumps(manifest, sort_keys=True).encode()
    (root / "deployment-manifest.json").write_bytes(raw)
    return {"bundle_root": root, "expected_commit": "a" * 40,
            "expected_manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "expected_wrapper_sha256": wrapper_sha}


def _reply():
    return json.dumps({"schema": "wd.q4-agent-wrapper.v1", "status": "ABSTAIN",
                       "scope": "wd.q4-shared-projected-events.v1",
                       "descriptor_sha256": "b" * 64, "memory_sha256": "c" * 64,
                       "content_mode": "hash_only_unresolved", "results": []}).encode()


def test_default_off_and_missing_transport_never_read_or_execute(plan):
    with pytest.raises(link.LaneLinkRefused, match="^disabled$"):
        link.invoke(lane=LANES[0], query_bytes=NoRead(), **plan)
    with pytest.raises(link.LaneLinkRefused, match="^transport_unavailable$"):
        link.invoke(enabled=True, lane=LANES[0], query_bytes=NoRead(), **plan)


@pytest.mark.parametrize("lane", LANES)
def test_five_declared_lanes_send_query_only_through_stdin_seam(plan, lane):
    calls = []
    query = b'{"situation":"synthetic-secret","time":"2026-01-01T00:00:00Z"}'

    def transport(*, wrapper, arguments, stdin_bytes):
        calls.append((wrapper, arguments, stdin_bytes))
        return _reply(), 0

    value = link.invoke(enabled=True, lane=lane, query_bytes=query, transport=transport, **plan)
    assert value["status"] == "ABSTAIN"
    assert len(calls) == 1 and calls[0][2] == query
    assert b"synthetic-secret" not in repr(calls[0][:2]).encode()
    assert calls[0][0] == str(plan["bundle_root"] / "Invoke-WdQ4Query.ps1")
    assert calls[0][1] == ("-BundleRoot", str(plan["bundle_root"]), "-ExpectedBundleCommit",
                           plan["expected_commit"], "-ExpectedManifestSha256", plan["expected_manifest_sha256"])
    assert "synthetic-secret" not in json.dumps(value)


@pytest.mark.parametrize("field", ["expected_commit", "expected_manifest_sha256", "expected_wrapper_sha256"])
def test_mismatched_or_missing_caller_pins_fail_before_query(plan, field):
    plan[field] = "0" * len(plan[field])
    with pytest.raises(link.LaneLinkRefused, match="^binding_refused$"):
        link.invoke(enabled=True, lane=LANES[0], query_bytes=NoRead(), transport=lambda **_: None, **plan)
    plan[field] = ""
    with pytest.raises(link.LaneLinkRefused, match="^binding_refused$"):
        link.invoke(enabled=True, lane=LANES[0], query_bytes=NoRead(), transport=lambda **_: None, **plan)


@pytest.mark.parametrize("name", ["deployment-manifest.json", "Invoke-WdQ4Query.ps1"])
def test_changed_file_fails_before_query(plan, name):
    (plan["bundle_root"] / name).write_bytes(b"tampered")
    with pytest.raises(link.LaneLinkRefused, match="^binding_refused$"):
        link.invoke(enabled=True, lane=LANES[0], query_bytes=NoRead(), transport=lambda **_: None, **plan)


def test_unknown_lane_and_oversized_input_refuse(plan):
    with pytest.raises(link.LaneLinkRefused, match="^invalid_lane$"):
        link.invoke(enabled=True, lane="unlisted", query_bytes=NoRead(), transport=lambda **_: None, **plan)
    with pytest.raises(link.LaneLinkRefused, match="^invalid_query$"):
        link.invoke(enabled=True, lane=LANES[0], query_bytes=b"x" * 65537, transport=lambda **_: None, **plan)


def test_stream_input_is_refused_without_reading(plan):
    def must_not_run(**_):
        pytest.fail("stream reached transport")

    with pytest.raises(link.LaneLinkRefused, match="^invalid_query$"):
        link.invoke(enabled=True, lane=LANES[0], query_bytes=NoRead(), transport=must_not_run, **plan)
    with pytest.raises(link.LaneLinkRefused, match="^invalid_query$"):
        link.invoke(enabled=True, lane=LANES[0], query_bytes=io.BytesIO(b"partial"), transport=must_not_run, **plan)


def test_duplicate_manifest_keys_are_not_accepted_even_if_the_bytes_are_pinned(plan):
    manifest = plan["bundle_root"] / "deployment-manifest.json"
    raw = manifest.read_bytes().replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1')
    manifest.write_bytes(raw)
    plan["expected_manifest_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(link.LaneLinkRefused, match="^binding_refused$"):
        link.invoke(enabled=True, lane=LANES[0], query_bytes=NoRead(), transport=lambda **_: None, **plan)


def test_uppercase_manifest_and_external_hash_pins_are_supported(plan):
    path = plan["bundle_root"] / "deployment-manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["files"]["Invoke-WdQ4Query.ps1"] = plan["expected_wrapper_sha256"].upper()
    raw = json.dumps(manifest).encode()
    path.write_bytes(raw)
    plan["expected_manifest_sha256"] = hashlib.sha256(raw).hexdigest().upper()
    plan["expected_wrapper_sha256"] = plan["expected_wrapper_sha256"].upper()
    result = link.invoke(enabled=True, lane=LANES[0], query_bytes=b"synthetic",
                         transport=lambda **_: (_reply(), 0), **plan)
    assert result["status"] == "ABSTAIN"


@pytest.mark.parametrize("reply", [b"not json", b'{"schema":"wd.q4-agent-wrapper.v1","status":"ABSTAIN","status":"ANSWER"}', b"x" * 65537],
                         ids=["invalid-json", "duplicate-keys", "oversized"])
def test_malformed_or_oversized_reply_refuses_without_echo(plan, reply):
    with pytest.raises(link.LaneLinkRefused, match="^transport_refused$") as exc:
        link.invoke(enabled=True, lane=LANES[0], query_bytes=b"synthetic-secret",
                    transport=lambda **_: (reply, 0), **plan)
    assert "synthetic-secret" not in repr(exc.value) and "not json" not in repr(exc.value)


def test_transport_error_is_sanitized(plan):
    def transport(**_):
        raise RuntimeError("synthetic-private-path")
    with pytest.raises(link.LaneLinkRefused, match="^transport_refused$") as exc:
        link.invoke(enabled=True, lane=LANES[0], query_bytes=b"synthetic-secret", transport=transport, **plan)
    assert "synthetic-private-path" not in repr(exc.value)


def test_resolved_reply_preserves_only_the_expected_wrapper_shape(plan):
    value = {"schema": "wd.q4-agent-wrapper.v2", "status": "ANSWER",
             "scope": "wd.q4-shared-projected-events.v1", "content_mode": "sanitized_projected_metadata",
             "memory_sha256": "1" * 64, "projection_sha256": "2" * 64,
             "policy_sha256": "3" * 64, "roster_sha256": "4" * 64,
             "results": [{"ref_sha256": "5" * 64, "doc_id_sha256": "6" * 64,
                          "ref_kind": "sha256_of_projected_metadata", "canonical_event": False,
                          "stored_text": "message done codex-lead-1 synthetic-task"}]}
    reply = json.dumps(value).encode()
    result = link.invoke(enabled=True, lane=LANES[0], query_bytes=b"synthetic-query",
                         transport=lambda **_: (reply, 0), **plan)
    assert result == value
