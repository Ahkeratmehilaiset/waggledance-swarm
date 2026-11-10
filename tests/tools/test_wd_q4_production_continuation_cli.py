"""The one-shot continuation CLI never prints input or retries a call."""
import importlib
import hashlib
import importlib.util
import base64
import json
import os
from pathlib import Path

import pytest

cli = importlib.import_module("tools.wd_q4_production_continuation_cli")
_spec = importlib.util.spec_from_file_location("continuation_test_fixture",
    Path(__file__).with_name("test_wd_q4_production_continuation.py"))
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
synthetic = _module.synthetic


def response(capsys):
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


def test_off_never_prepares_or_continues(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_prepare", lambda *_: pytest.fail("OFF opened inputs"))
    assert cli.main([]) == 0
    assert response(capsys) == {"status": "OFF"}


@pytest.mark.parametrize("args", [["--enable"], ["--enable", "--config", "x", "--sha256", "bad"]])
def test_invalid_arguments_never_prepare(args, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_prepare", lambda *_: pytest.fail("invalid args opened input"))
    assert cli.main(args) == 2
    assert response(capsys) == {"status": "REFUSED"}


def test_single_call_and_only_allowed_receipt_fields(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(cli, "_prepare", lambda *_: {"enabled": True, "tuple_bytes": b"private"})
    def run(**kw):
        seen.append(kw)
        return {"status": "partial_ready_prefix", "runner_calls": 1, "pointer_sha256": "a" * 64,
                "runner_receipt_sha256": "b" * 64, "coverage": "partial_ready_prefix",
                "source_authenticity": "UNKNOWN", "freshness": "UNKNOWN"}
    monkeypatch.setattr(cli.continuation, "continue_once", run)
    assert cli.main(["--enable", "--config", "synthetic", "--sha256", "f" * 64]) == 0
    assert len(seen) == 1
    assert response(capsys) == {"status": "partial_ready_prefix", "pointer_sha256": "a" * 64,
                                "runner_receipt_sha256": "b" * 64}


@pytest.mark.parametrize("fault,code,status", [("refused", 2, "REFUSED"), ("uncertain", 3, "UNKNOWN"),
                                                ("unexpected", 3, "UNKNOWN"), ("bad_result", 3, "UNKNOWN")])
def test_failure_is_sanitized_and_never_retried(monkeypatch, capsys, fault, code, status):
    calls = []
    monkeypatch.setattr(cli, "_prepare", lambda *_: {"enabled": True})
    def run(**kwargs):
        calls.append(kwargs)
        if fault == "refused": raise cli.continuation.ContinuationRefused("private input")
        if fault == "uncertain": raise cli.continuation.ContinuationUnknown("private input")
        if fault == "unexpected": raise RuntimeError("private input")
        return {"status": "partial_ready_prefix", "pointer_sha256": "secret"}
    monkeypatch.setattr(cli.continuation, "continue_once", run)
    assert cli.main(["--enable", "--config", "synthetic", "--sha256", "f" * 64]) == code
    assert len(calls) == 1
    assert response(capsys) == {"status": status}


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def synthetic_config(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("real continuation config requires Windows C: path and identity checks")
    bundle = tmp_path / "bundle"
    code = bundle / "tools-bootstrap/tools"
    code.mkdir(parents=True)
    for module in (cli, cli.continuation):
        target = code / Path(module.__file__).name
        target.write_bytes(Path(module.__file__).read_bytes())
        monkeypatch.setattr(module, "__file__", str(target))
    fleet = b'{"synthetic":true}'
    (bundle / "wd-fleet.json").write_bytes(fleet)
    files = {"wd-fleet.json": digest(fleet)}
    for path in code.iterdir():
        files["tools-bootstrap/tools/" + path.name] = digest(path.read_bytes())
    manifest = json.dumps({"schema_version": 1, "files": files}).encode()
    (bundle / "deployment-manifest.json").write_bytes(manifest)
    policy, public = b'{"policy":1}', b'{"public":1}'
    (tmp_path / "policy.json").write_bytes(policy)
    (tmp_path / "public.json").write_bytes(public)
    source_pin = {"identity": "synthetic-id", "prefix_bytes": 4, "prefix_sha256": digest(b"row\n")}
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    route = json.dumps({"checkpoint_root": str(checkpoint)}).encode()
    tuple_raw = json.dumps({"independent_source_pin": source_pin,
        "route": {"bytes_b64": base64.b64encode(route).decode(), "sha256": digest(route)}}).encode()
    (tmp_path / "tuple.json").write_bytes(tuple_raw)
    config = {"schema": "wd.q4-continuation-cli.v1", "bundle_root": str(bundle),
              "manifest_sha256": digest(manifest), "tuple_path": str(tmp_path / "tuple.json"),
              "tuple_sha256": digest(tuple_raw), "policy_path": str(tmp_path / "policy.json"),
              "policy_sha256": digest(policy), "public_policy_path": str(tmp_path / "public.json"),
              "public_policy_sha256": digest(public), "fleet_sha256": digest(fleet),
              "pointer_sha256": "c" * 64, "source_identity": source_pin["identity"],
              "source_prefix_bytes": source_pin["prefix_bytes"], "source_prefix_sha256": source_pin["prefix_sha256"]}
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", digest(manifest))
    def write():
        raw = json.dumps(config).encode()
        path = tmp_path / "config.json"
        path.write_bytes(raw)
        return str(path), digest(raw)
    path, pin = write()
    prepared = cli._prepare(path, pin)
    assert prepared["expected_pointer_sha256"] == config["pointer_sha256"]
    assert prepared["expected_tuple_sha256"] == config["tuple_sha256"]
    return config, write, tmp_path


def test_real_prepare_reads_independent_bound_inputs(synthetic_config):
    config, write, _ = synthetic_config
    path, pin = write()
    result = cli._prepare(path, pin)
    assert result["expected_pointer_sha256"] == config["pointer_sha256"]
    assert result["expected_tuple_sha256"] == config["tuple_sha256"]
    assert result["fleet_bytes"] == b'{"synthetic":true}'


@pytest.mark.parametrize("change", ["source_identity", "source_prefix_bytes", "source_prefix_sha256",
                                    "tuple_sha256", "policy_sha256", "public_policy_sha256", "fleet_sha256",
                                    "manifest_sha256", "extra", "bad_pointer"])
def test_bad_independent_pin_refuses_before_continuation(synthetic_config, change, monkeypatch, capsys):
    config, write, _ = synthetic_config
    if change == "source_identity": config[change] = "other"
    elif change == "source_prefix_bytes": config[change] += 1
    elif change == "extra": config["untrusted"] = True
    elif change == "bad_pointer": config["pointer_sha256"] = "bad"
    else: config[change] = "f" * 64
    path, pin = write()
    monkeypatch.setattr(cli.continuation, "continue_once", lambda **_: pytest.fail("called"))
    assert cli.main(["--enable", "--config", path, "--sha256", pin]) == 2
    assert response(capsys) == {"status": "REFUSED"}


@pytest.mark.skipif(os.name != "nt", reason="real continuation fixtures require Windows C: paths")
def test_cli_reaches_real_continuation_once_with_synthetic_runner(synthetic, synthetic_config, monkeypatch, capsys):
    _, args, state, document, *_ = synthetic
    config, write, tmp = synthetic_config
    for key, name in (("tuple_bytes", "tuple.json"), ("policy_bytes", "policy.json"),
                      ("public_policy_bytes", "public.json")):
        (tmp / name).write_bytes(args[key])
    bundle = Path(config["bundle_root"])
    (bundle / "wd-fleet.json").write_bytes(args["fleet_bytes"])
    manifest_path = bundle / "deployment-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"]["wd-fleet.json"] = digest(args["fleet_bytes"])
    manifest_bytes = json.dumps(manifest).encode()
    manifest_path.write_bytes(manifest_bytes)
    source_pin = document["independent_source_pin"]
    config.update(manifest_sha256=digest(manifest_bytes), tuple_sha256=args["expected_tuple_sha256"],
                  policy_sha256=digest(args["policy_bytes"]), public_policy_sha256=digest(args["public_policy_bytes"]),
                  fleet_sha256=args["expected_fleet_sha256"], pointer_sha256=args["expected_pointer_sha256"],
                  source_identity=source_pin["identity"], source_prefix_bytes=source_pin["prefix_bytes"],
                  source_prefix_sha256=source_pin["prefix_sha256"])
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", digest(manifest_bytes))
    path, pin = write()
    assert cli.main(["--enable", "--config", path, "--sha256", pin]) == 0
    assert response(capsys)["status"] == "partial_ready_prefix"
    assert len(state["calls"]) == 1


@pytest.mark.parametrize("variant", ["direct", "nested", "short_alias"])
def test_direct_cli_refuses_private_tuple_in_checkpoint(synthetic_config, variant, monkeypatch, capsys):
    config, write, tmp = synthetic_config
    parent = tmp / "checkpoint" / "nested" if variant == "nested" else tmp / "checkpoint"
    parent.mkdir(exist_ok=True)
    target = parent / "private-tuple.json"
    target.write_bytes((tmp / "tuple.json").read_bytes())
    target_path = str(target)
    if variant == "short_alias":
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetShortPathNameW(target_path, buffer, len(buffer)):
            pytest.skip("Windows short path unavailable")
        if os.path.normcase(buffer.value) == os.path.normcase(target_path):
            pytest.skip("Windows did not create a distinct short path")
        target_path = buffer.value
    config["tuple_path"] = target_path
    path, pin = write()
    monkeypatch.setattr(cli.continuation, "continue_once", lambda **_: pytest.fail("unsafe continuation"))
    assert cli.main(["--enable", "--config", path, "--sha256", pin]) == 2
    assert response(capsys) == {"status": "REFUSED"}


def test_explicit_v2_config_binds_cutoff_and_previous_cutoff(synthetic_config):
    config, write, _ = synthetic_config
    config.update(schema="wd.q4-continuation-cli.v2", cutoff_utc="2026-10-31T13:02:00Z",
                  previous_cutoff_utc="2026-10-31T13:00:00Z")
    path, pin = write()
    result = cli._prepare(path, pin)
    assert result["cutoff_utc"] == config["cutoff_utc"]
    assert result["previous_cutoff_utc"] == config["previous_cutoff_utc"]


@pytest.mark.parametrize("cutoff,previous", [("bad", "2026-10-31T13:00:00Z"),
                                             ("2026-10-31T12:59:00Z", "2026-10-31T13:00:00Z")])
def test_invalid_v2_cutoff_is_refused(synthetic_config, cutoff, previous, monkeypatch, capsys):
    config, write, _ = synthetic_config
    config.update(schema="wd.q4-continuation-cli.v2", cutoff_utc=cutoff, previous_cutoff_utc=previous)
    path, pin = write()
    monkeypatch.setattr(cli.continuation, "continue_once", lambda **_: pytest.fail("called"))
    assert cli.main(["--enable", "--config", path, "--sha256", pin]) == 2
    assert response(capsys) == {"status": "REFUSED"}


def test_v2_receipt_discloses_only_cutoff_and_proof_hashes(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_prepare", lambda *_: {"enabled": True, "cutoff_utc": "2026-10-31T13:02:00Z",
                                                      "previous_cutoff_utc": "2026-10-31T13:00:00Z"})
    result = {"status": "DEFERRED", "runner_calls": 1, "pointer_sha256": "a" * 64,
              "runner_receipt_sha256": "b" * 64, "coverage": "partial_ready_prefix",
              "source_authenticity": "UNKNOWN", "freshness": "UNKNOWN",
              "cutoff_utc": "2026-10-31T13:02:00Z", "previous_cutoff_utc": "2026-10-31T13:00:00Z",
              "route_sha256": "c" * 64, "cutoff_monotonicity": "CALLER_ASSERTED_REPLAY_CRASH_UNPROVEN"}
    monkeypatch.setattr(cli.continuation, "continue_once", lambda **_: result)
    assert cli.main(["--enable", "--config", "fixture", "--sha256", "f" * 64]) == 0
    assert response(capsys) == {key: result[key] for key in ("status", "pointer_sha256", "runner_receipt_sha256",
        "cutoff_utc", "previous_cutoff_utc", "route_sha256", "cutoff_monotonicity")}


def test_v3_receipt_binds_watermark_without_disclosing_private_inputs(monkeypatch, capsys):
    inputs = {"enabled": True, "watermark_enabled": True, "cutoff_utc": "2026-10-31T13:02:00Z",
              "previous_cutoff_utc": "2026-10-31T13:00:00Z", "expected_watermark_record_sha256": None}
    monkeypatch.setattr(cli, "_prepare", lambda *_: inputs)
    result = {"status": "DEFERRED", "runner_calls": 1, "pointer_sha256": "a" * 64,
              "runner_receipt_sha256": "b" * 64, "coverage": "partial_ready_prefix",
              "source_authenticity": "UNKNOWN", "freshness": "UNKNOWN",
              "cutoff_utc": inputs["cutoff_utc"], "previous_cutoff_utc": inputs["previous_cutoff_utc"],
              "route_sha256": "c" * 64, "watermark_record_sha256": "d" * 64,
              "cutoff_monotonicity": "COOPERATIVE_LEASE_PERSISTED_CRASH_UNKNOWN"}
    monkeypatch.setattr(cli.continuation, "continue_once", lambda **_: result)
    assert cli.main(["--enable", "--config", "fixture", "--sha256", "f" * 64]) == 0
    assert response(capsys) == {key: result[key] for key in ("status", "pointer_sha256", "runner_receipt_sha256",
        "cutoff_utc", "previous_cutoff_utc", "route_sha256", "cutoff_monotonicity", "watermark_record_sha256")}


def test_v3_config_requires_manifest_pinned_watermark(synthetic_config, monkeypatch):
    from tools import wd_q4_cutoff_watermark as watermark
    config, write, tmp = synthetic_config
    code = Path(config["bundle_root"]) / "tools-bootstrap/tools/wd_q4_cutoff_watermark.py"
    code.write_bytes(Path(watermark.__file__).read_bytes())
    monkeypatch.setattr(watermark, "__file__", str(code))
    manifest_path = Path(config["bundle_root"]) / "deployment-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"]["tools-bootstrap/tools/wd_q4_cutoff_watermark.py"] = digest(code.read_bytes())
    manifest_bytes = json.dumps(manifest).encode()
    manifest_path.write_bytes(manifest_bytes)
    config.update(schema="wd.q4-continuation-cli.v3", manifest_sha256=digest(manifest_bytes),
                  cutoff_utc="2026-10-31T13:02:00Z", previous_cutoff_utc="2026-10-31T13:00:00Z",
                  watermark_previous_record_sha256=None)
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", digest(manifest_bytes))
    path, pin = write()
    prepared = cli._prepare(path, pin)
    assert prepared["watermark_enabled"] is True
    assert prepared["expected_watermark_record_sha256"] is None
    code.write_bytes(code.read_bytes() + b"# tampered\n")
    with pytest.raises(Exception):
        cli._prepare(path, pin)
