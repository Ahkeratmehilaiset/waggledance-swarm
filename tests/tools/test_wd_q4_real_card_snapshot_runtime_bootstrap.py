"""Synthetic checks for the pinned E5 real-card producer bootstrap; never a live bundle, model or source.

The bootstrap runs isolated (-I -S -B) from a deployed tools-bootstrap package, re-verifies the package against the
deployment manifest, pins the interpreter, verifies the exact runtime manifest (site and model trees), binds the
request's trusted model root to that runtime model, and only then imports the packaged real-card snapshot CLI with
the verified runtime site appended. A synthetic CLI stands in for the producer; one test imports the real CLI copy.
"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAME = "wd_q4_real_card_snapshot_runtime_bootstrap.py"
HELPER = "wd_q4_live_update_bootstrap.py"
CLI = "wd_q4_real_card_snapshot_cli.py"
SUCCESS = {"status": "VALIDATED_STAGED", "pointer_sha256": "a" * 64, "memory_sha256": "b" * 64,
           "descriptor_sha256": "c" * 64, "completeness": "UNKNOWN", "source_authentication": "UNKNOWN",
           "publisher_authentication": "UNKNOWN", "freshness": "UNKNOWN", "rejected_invalid_expiry": 0,
           "source_mode": "synthetic_fixture"}

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows C: drive paths")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def test_default_off_reads_nothing(monkeypatch, capsys):
    mod = importlib.import_module("tools.wd_q4_real_card_snapshot_runtime_bootstrap")
    monkeypatch.setattr(mod, "_read", lambda *_a, **_k: pytest.fail("OFF read"))
    assert mod.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "OFF"}


def synthetic_cli(body):
    return ("import sys\n"
            "def main(args):\n"
            " import synthetic_runtime_dependency\n"
            " assert synthetic_runtime_dependency.VALUE == 42\n" + body)


def success_body(expected_args):
    return (" assert args == " + repr(expected_args) + "\n"
            " print(" + repr(json.dumps(SUCCESS, sort_keys=True, separators=(",", ":"))) + ")\n"
            " return 0\n")


@pytest.fixture
def case(tmp_path):
    from sandbox.mind.step2 import runtime_builder
    bundle = tmp_path / "bundle"
    code = bundle / "tools-bootstrap"
    (code / "tools").mkdir(parents=True)
    (code / "sandbox/mind/step2").mkdir(parents=True)
    for name in (NAME, HELPER):
        shutil.copyfile(ROOT / "tools" / name, code / "tools" / name)
    (code / "tools/__init__.py").write_text("", encoding="ascii")
    verifier = code / "sandbox/mind/step2/runtime_manifest.py"
    shutil.copyfile(ROOT / "sandbox/mind/step2/runtime_manifest.py", verifier)
    runtime = tmp_path / "runtime"
    (runtime / "assets/code").mkdir(parents=True)
    (runtime / "site").mkdir()
    (runtime / "model").mkdir()
    (runtime / "assets/code/stub.txt").write_text("synthetic code", encoding="ascii")
    (runtime / "site/synthetic_runtime_dependency.py").write_text("VALUE=42\n", encoding="ascii")
    (runtime / "model/model.txt").write_text("synthetic model", encoding="ascii")
    runtime_manifest = tmp_path / "runtime-manifest.json"
    built = runtime_builder.build_runtime_manifest(runtime, runtime_manifest, generation="a" * 40, revision="b" * 40)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    request = inputs / "request.json"
    request.write_bytes(encode({"schema": "wd.q4-real-card-snapshot-request.v1", "source_mode": "synthetic_fixture",
        "inputs": {}, "source_path": str(tmp_path / "source.jsonl"), "source_generation": {},
        "cursor": {}, "now_utc": "2026-11-07T08:00:00Z", "output_root": str(tmp_path / "out"),
        "generation_name": "g1", "git": {}, "encoder": {}, "trusted_model_root": str(runtime / "model")}))
    item = {"tmp": tmp_path, "bundle": bundle, "code": code, "runtime": runtime, "request": request,
            "spec": inputs / "runtime.json", "verifier": verifier, "cli": code / "tools" / CLI,
            "bootstrap": code / "tools" / NAME,
            "runtime_doc": {"schema": "wd.q4-real-card-runtime.v1", "bundle_root": str(bundle),
                "interpreter_path": sys.executable, "interpreter_sha256": sha(Path(sys.executable).read_bytes()),
                "runtime_root": str(runtime), "runtime_manifest_path": str(runtime_manifest),
                "runtime_manifest_sha256": built["manifest_sha256"], "runtime_generation": "a" * 40,
                "runtime_revision": "b" * 40, "verifier_sha256": sha(verifier.read_bytes())}}
    item["request_pin"] = sha(request.read_bytes())
    write_cli(item, success_body(expected_args(item)))
    return item


def expected_args(item):
    return ["--enabled", "--request", str(item["request"]), "--request-sha256", item["request_pin"]]


def write_cli(item, body, raw=None):
    item["cli"].write_text(raw if raw is not None else synthetic_cli(body), encoding="ascii")
    repin(item)


def repin(item):
    code = item["code"]
    files = {"tools-bootstrap/" + p.relative_to(code).as_posix(): sha(p.read_bytes()).upper()
             for p in code.rglob("*") if p.is_file()}
    manifest = item["bundle"] / "deployment-manifest.json"
    manifest.write_bytes(encode({"schema_version": 1, "source_commit": "c" * 40, "files": files}))
    item["manifest_pin"] = sha(manifest.read_bytes())
    item["runtime_doc"]["manifest_sha256"] = item["manifest_pin"]
    write_spec(item)


def write_spec(item):
    item["spec"].write_bytes(encode(item["runtime_doc"]))
    item["spec_pin"] = sha(item["spec"].read_bytes())


def execute(item, *, flags=("-I", "-S", "-B"), env_pin=None, request_pin=None):
    env = dict(os.environ, WD_REBOOT_EXPECTED_MANIFEST_HASH=env_pin or item["manifest_pin"])
    return subprocess.run([sys.executable, *flags, str(item["bootstrap"]), "--enable",
        "--request", str(item["request"]), "--request-sha256", request_pin or item["request_pin"],
        "--manifest-sha256", item["manifest_pin"], "--runtime", str(item["spec"]),
        "--runtime-sha256", item["spec_pin"]], capture_output=True, text=True, timeout=60, env=env)


def test_verified_runtime_reaches_the_packaged_cli_with_the_exact_request(case):
    result = execute(case)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == json.dumps(SUCCESS, sort_keys=True, separators=(",", ":")) + "\n"
    assert result.stderr == ""


def refused(item, **kwargs):
    result = execute(item, **kwargs)
    assert (result.returncode, result.stdout, result.stderr) == (2, '{"status":"REFUSED"}\n', "")


@pytest.mark.parametrize("fault", ["site", "model", "assets", "request_bytes", "request_schema", "model_root",
                                   "model_root_missing", "genesis_runtime", "runtime_extra_key", "interpreter",
                                   "verifier", "unlisted", "bootstrap", "helper", "cli_unlisted_edit",
                                   "bundle_root", "runtime_manifest_pin", "generation"])
def test_bad_or_unbound_inputs_refuse_before_the_cli(case, fault):
    marker = case["tmp"] / "cli-entered"
    write_cli(case, " open(" + repr(str(marker)) + ", 'x').close()\n" + success_body(expected_args(case)))
    doc = case["runtime_doc"]
    if fault == "site": (case["runtime"] / "site/synthetic_runtime_dependency.py").write_text("VALUE=1")
    if fault == "model": (case["runtime"] / "model/model.txt").write_text("tampered")
    if fault == "assets": (case["runtime"] / "assets/code/extra.txt").write_text("extra")
    if fault == "request_bytes": case["request"].write_bytes(case["request"].read_bytes() + b" ")
    if fault in ("request_schema", "model_root", "model_root_missing"):
        value = json.loads(case["request"].read_bytes())
        if fault == "request_schema": value["schema"] = "wd.q4-genesis-cli.v1"
        if fault == "model_root": value["trusted_model_root"] = str(case["runtime"] / "site")
        if fault == "model_root_missing": del value["trusted_model_root"]
        case["request"].write_bytes(encode(value))
        case["request_pin"] = sha(case["request"].read_bytes())
    if fault == "genesis_runtime": doc["schema"] = "wd.q4-genesis-runtime.v1"
    if fault == "runtime_extra_key": doc["extra_site"] = str(case["tmp"])
    if fault == "interpreter": doc["interpreter_sha256"] = "0" * 64
    if fault == "verifier": doc["verifier_sha256"] = "0" * 64
    if fault == "unlisted": (case["code"] / "tools/extra.py").write_text("pass")
    if fault == "bootstrap": case["bootstrap"].write_bytes(case["bootstrap"].read_bytes() + b"\n")
    if fault == "helper":
        helper = case["code"] / "tools" / HELPER
        helper.write_bytes(helper.read_bytes() + b"\n")
    if fault == "cli_unlisted_edit": case["cli"].write_bytes(case["cli"].read_bytes() + b"\n")
    if fault == "bundle_root": doc["bundle_root"] = str(case["runtime"])
    if fault == "runtime_manifest_pin": doc["runtime_manifest_sha256"] = "0" * 64
    if fault == "generation": doc["runtime_generation"] = "c" * 40
    if fault in ("genesis_runtime", "runtime_extra_key", "interpreter", "verifier", "bundle_root",
                 "runtime_manifest_pin", "generation"):
        write_spec(case)
    refused(case)
    assert not marker.exists()


def test_manifest_pin_must_match_the_anchored_environment(case):
    refused(case, env_pin="0" * 64)


def test_the_bootstrap_requires_isolated_no_site_no_bytecode_flags(case):
    refused(case, flags=("-I", "-S"))
    refused(case, flags=("-S", "-B"))


def test_a_wrong_request_pin_refuses(case):
    refused(case, request_pin="0" * 64)


def test_the_cli_refusal_passes_through(case):
    write_cli(case, " print('{\"status\":\"REFUSED\"}', file=sys.stderr)\n return 2\n")
    refused(case)


def unknown(item):
    result = execute(item)
    assert (result.returncode, result.stdout, result.stderr) == (3, '{"status":"UNKNOWN"}\n', "")
    assert "synthetic-canary" not in result.stdout + result.stderr


ANSWER = json.dumps(SUCCESS, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("mode", ["exception", "interrupt", "exit_three", "refused_on_stdout", "stderr_noise",
                                  "extra_key", "missing_key", "wrong_status", "known_completeness",
                                  "source_mode", "bad_pin", "expiry_range", "two_lines", "exit_zero_refused",
                                  "request_rewritten", "foreign_project_module", "foreign_module_hidden",
                                  "foreign_module_by_location", "multiline_json"])
def test_any_doubt_after_the_cli_starts_is_unknown(case, mode):
    def answer(**changes):
        value = dict(SUCCESS, **changes)
        return " print(" + repr(json.dumps(value, sort_keys=True, separators=(",", ":"))) + ")\n return 0\n"
    bodies = {
        "exception": " raise ValueError('synthetic-canary')\n",
        "interrupt": " raise KeyboardInterrupt('synthetic-canary')\n",
        "exit_three": " print('{\"status\":\"UNKNOWN\"}', file=sys.stderr)\n return 3\n",
        "refused_on_stdout": " print('{\"status\":\"REFUSED\"}')\n return 2\n",
        "stderr_noise": " print('synthetic-canary', file=sys.stderr)\n print(" + repr(ANSWER) + ")\n return 0\n",
        "extra_key": answer(extra="synthetic-canary"),
        "wrong_status": answer(status="PUBLISHED"),
        "known_completeness": answer(completeness="COMPLETE"),
        "source_mode": answer(source_mode="operator_authorized_source"),
        "bad_pin": answer(pointer_sha256="A" * 64),
        "expiry_range": answer(rejected_invalid_expiry=4097),
        "two_lines": " print(" + repr(ANSWER) + ")\n print(" + repr(ANSWER) + ")\n return 0\n",
        "exit_zero_refused": " print('{\"status\":\"REFUSED\"}', file=sys.stderr)\n return 0\n",
        "request_rewritten": (" open(" + repr(str(case["request"])) + ", 'ab').write(b' ')\n"
                              " print(" + repr(ANSWER) + ")\n return 0\n"),
        "foreign_project_module": " import waggledance_synthetic_foreign.canary\n print(" + repr(ANSWER) + ")\n return 0\n",
        # Only the import guard sees a foreign project module that is dropped from sys.modules afterwards.
        "foreign_module_hidden": (" import waggledance_synthetic_foreign.canary\n"
                                  " del sys.modules['waggledance_synthetic_foreign.canary']\n"
                                  " del sys.modules['waggledance_synthetic_foreign']\n"
                                  " print(" + repr(ANSWER) + ")\n return 0\n"),
        # Only the post-run origin check sees a project-named module loaded by location, bypassing finders.
        "foreign_module_by_location": (" import importlib.util\n"
                                       " spec = importlib.util.spec_from_file_location('waggledance_synthetic_foreign', "
                                       + repr(str(case["runtime"] / "site/waggledance/canary.py")) + ")\n"
                                       " module = importlib.util.module_from_spec(spec)\n"
                                       " spec.loader.exec_module(module)\n"
                                       " sys.modules['waggledance_synthetic_foreign'] = module\n"
                                       " print(" + repr(ANSWER) + ")\n return 0\n"),
        "multiline_json": " print(" + repr(json.dumps(SUCCESS, sort_keys=True, indent=1)) + ")\n return 0\n",
    }
    if mode == "missing_key":
        value = dict(SUCCESS)
        del value["freshness"]
        body = " print(" + repr(json.dumps(value, sort_keys=True, separators=(",", ":"))) + ")\n return 0\n"
    else:
        body = bodies[mode]
    if mode in ("foreign_project_module", "foreign_module_hidden", "foreign_module_by_location"):
        body = body.replace("waggledance_synthetic_foreign", "waggledance")
        package = case["runtime"] / "site/waggledance"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="ascii")
        (package / "canary.py").write_text("VALUE = 'synthetic-canary'\n", encoding="ascii")
        rebuild_runtime(case)
    write_cli(case, body)
    unknown(case)


def rebuild_runtime(item):
    from sandbox.mind.step2 import runtime_builder
    doc = item["runtime_doc"]
    path = Path(doc["runtime_manifest_path"])
    path.unlink()
    built = runtime_builder.build_runtime_manifest(item["runtime"], path, generation="a" * 40, revision="b" * 40)
    doc["runtime_manifest_sha256"] = built["manifest_sha256"]
    write_spec(item)


def test_the_real_packaged_cli_is_imported_from_the_bundle_and_its_refusal_passes_through(case):
    shutil.copyfile(ROOT / "tools" / CLI, case["cli"])
    repin(case)
    refused(case)  # the real CLI refuses this synthetic request (empty inputs) before any producer import


def test_the_real_packaged_cli_must_match_the_manifest(case):
    shutil.copyfile(ROOT / "tools" / CLI, case["cli"])
    repin(case)
    case["cli"].write_bytes(case["cli"].read_bytes().replace(b"real_card_cli_refused", b"real_card_cli_refusex"))
    refused(case)
