"""Default-off staged runtime-plan caller: plan_runtime_inputs bound to a frozen v3 fixed plan.

The fixed plan (built by the real producer, parsed by the real transport parser) supplies the code commit
(bundle_commit), the runtime identity (runtime_generation, runtime_revision) and the staged backend pin
(backend_sha256). Synthetic temporary repositories and trees; one test reads this repository's committed blobs.
No runtime build, install or deployment.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess

import pytest

from tools import build_q4_runtime_plan as planner
from tools import wd_q4_fixed_plan_producer as producer
from tests.tools.test_build_q4_runtime_plan import GIT, WEIGHTS, make_repo, make_tree, sha
from tests.tools.test_build_q4_runtime_plan_staged_backend import FILES, HELPER, HELPER_SOURCE, LAZY
from tests.tools.test_wd_q4_staged_fixed_plan import staged_args

pytestmark = [pytest.mark.skipif(GIT is None, reason="git executable required"),
              pytest.mark.skipif(os.name != "nt", reason="Windows fixed-peer paths")]

GENERATION, REVISION = "1" * 40, "2" * 40


def module():
    from tools import wd_q4_staged_runtime_plan
    return wd_q4_staged_runtime_plan


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def fixed_plan(commit, backend=sha(HELPER_SOURCE), **changes):
    args = dict(staged_args(), backend_sha256=backend, bundle_commit=commit, runtime_generation=GENERATION,
                runtime_revision=REVISION, **changes)
    return producer.build_staged_fixed_plan(enabled=True, **args)


def inputs(tmp_path, files=FILES):
    repo, commit = make_repo(tmp_path, files)
    site = make_tree(tmp_path / "site", {"pkg.py": b"VALUE = 1\n"})
    model = make_tree(tmp_path / "model", {"model.safetensors": WEIGHTS})
    planner_args = dict(git_executable=GIT, repository=str(repo), code_roots=["tools/wd_q4_fixed_peer.py"],
                        site_root=str(site), model_root=str(model), expected_model_files=["model.safetensors"],
                        expected_weights_sha256=sha(WEIGHTS), max_bytes=1024 * 1024)
    return commit, planner_args


def call(plan, planner_args, **changes):
    kwargs = dict(enabled=True, fixed_plan_bytes=plan["plan_bytes"], expected_fixed_plan_sha256=plan["plan_sha256"],
                  **planner_args)
    kwargs.update(changes)
    return module().plan_staged_runtime(**kwargs)


def refused(plan, planner_args, token, **changes):
    with pytest.raises(module().StagedRuntimePlanRefused) as exc:
        call(plan, planner_args, **changes)
    assert exc.value.args == (token,)
    assert exc.value.__cause__ is None and exc.value.__context__ is None


def test_default_off_reads_nothing():
    class NoTouch:
        def __getattribute__(self, name):
            raise AssertionError("disabled path inspected input")
    assert module().plan_staged_runtime(fixed_plan_bytes=NoTouch(), repository=NoTouch()) == {"status": "OFF"}


def test_the_runtime_plan_is_bound_to_the_frozen_fixed_plan(tmp_path):
    commit, planner_args = inputs(tmp_path)
    plan = fixed_plan(commit)
    result = call(plan, planner_args)
    document = json.loads(plan["plan_bytes"])
    expected = planner.plan_runtime_inputs(enabled=True, commit=commit, generation=GENERATION, revision=REVISION,
                                           staged_backend_sha256=sha(HELPER_SOURCE), **planner_args)
    assert result == {"schema": "wd.q4-staged-runtime-plan.v1", "status": "CANDIDATE_UNVERIFIED",
                      "fixed_plan_sha256": plan["plan_sha256"], "bundle_commit": commit,
                      "backend_sha256": sha(HELPER_SOURCE),
                      "expected_runtime_manifest_sha256": document["runtime_manifest_sha256"],
                      "runtime_plan": expected}
    runtime = result["runtime_plan"]
    assert (runtime["commit"], runtime["generation"], runtime["revision"]) == (commit, GENERATION, REVISION)
    assert runtime["staged_backend"] == {"path": HELPER, "size": len(HELPER_SOURCE), "sha256": sha(HELPER_SOURCE)}
    assert LAZY | {HELPER} <= set(runtime["code_paths"])


def test_a_wrong_backend_pin_in_the_fixed_plan_refuses(tmp_path):
    commit, planner_args = inputs(tmp_path)
    refused(fixed_plan(commit, backend=sha(HELPER_SOURCE + b"# other\n")), planner_args, "staged_backend_mismatch")


def test_a_fixed_plan_naming_another_commit_refuses(tmp_path):
    commit, planner_args = inputs(tmp_path)
    refused(fixed_plan("f" * 40), planner_args, "commit_invalid")


@pytest.mark.parametrize("case", ["helper_missing", "lazy_import_missing"])
def test_a_missing_staged_closure_refuses(tmp_path, case):
    if case == "helper_missing":
        files = {key: value for key, value in FILES.items() if key != HELPER}
        backend = sha(HELPER_SOURCE)
    else:
        source = HELPER_SOURCE + b"\n\ndef later():\n    from tools.ghost import value\n"
        files, backend = dict(FILES, **{HELPER: source}), sha(source)
    commit, planner_args = inputs(tmp_path, files)
    refused(fixed_plan(commit, backend=backend), planner_args, "code_closure_incomplete")


def test_the_fixed_plan_bytes_must_match_their_pin(tmp_path):
    commit, planner_args = inputs(tmp_path)
    plan = fixed_plan(commit)
    refused(plan, planner_args, "fixed_plan_refused", expected_fixed_plan_sha256="0" * 64)
    refused(plan, planner_args, "fixed_plan_refused", expected_fixed_plan_sha256="A" * 64)
    refused(plan, planner_args, "fixed_plan_refused", fixed_plan_bytes=plan["plan_bytes"] + b" ")


def test_a_non_card_fixed_plan_is_not_a_staged_route(tmp_path):
    commit, planner_args = inputs(tmp_path)
    document = json.loads(fixed_plan(commit)["plan_bytes"])
    v2 = producer.build_fixed_plan(enabled=True, **{key: document[key] for key in (
        "interpreter_sha256", "peer_code_sha256", "verifier_sha256", "runtime_manifest_sha256", "bundle_sha256",
        "expected_pointer_sha256", "policy_sha256", "roster_sha256", "expected_public_policy_sha256",
        "expected_source_sha256", "expected_encoder_sha256", "runtime_generation", "runtime_revision",
        "runtime_root", "runtime_manifest_path", "bundle_bytes", "approved_tasks")})
    refused(v2, planner_args, "fixed_plan_refused")
    plain = planner.plan_runtime_inputs(enabled=True, commit=commit, generation=GENERATION, revision=REVISION,
                                        **planner_args)
    assert HELPER not in plain["code_paths"] and "staged_backend" not in plain


@pytest.mark.parametrize("flag", [None, 1, "yes"])
def test_enabled_must_be_the_bool_true(tmp_path, flag):
    commit, planner_args = inputs(tmp_path)
    refused(fixed_plan(commit), planner_args, "staged_runtime_plan_refused", enabled=flag)


def run_cli(argv, capsys):
    code = module().main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def write_request(tmp_path, plan, planner_args, **changes):
    plan_path = tmp_path / "fixed-plan.json"
    if not plan_path.exists():
        plan_path.write_bytes(plan["plan_bytes"])
    document = dict(schema="wd.q4-staged-runtime-plan-request.v1", fixed_plan_path=str(plan_path),
                    fixed_plan_sha256=plan["plan_sha256"], extra_code_paths=[], expected_site_pins=None,
                    timeout_seconds=60, **planner_args)
    document.update(changes)
    raw = canon(document)
    path = tmp_path / ("request-%s.json" % sha(raw)[:12])
    path.write_bytes(raw)
    return str(path), sha(raw)


def test_cli_without_arguments_is_off(capsys):
    assert run_cli([], capsys) == (0, '{"status":"OFF"}\n', "")


def test_cli_plans_from_one_pinned_request_and_prints_one_canonical_line(tmp_path, capsys):
    commit, planner_args = inputs(tmp_path)
    plan = fixed_plan(commit)
    path, pin = write_request(tmp_path, plan, planner_args)
    code, out, err = run_cli(["--enabled", "--request", path, "--request-sha256", pin], capsys)
    assert code == 0 and err == ""
    result = json.loads(out)
    assert out == canon(result).decode("ascii") + "\n"
    assert result == call(plan, planner_args)


def test_cli_refusals_print_only_the_fixed_token(tmp_path, capsys):
    commit, planner_args = inputs(tmp_path)
    plan = fixed_plan(commit, backend=sha(b"other"))
    path, pin = write_request(tmp_path, plan, planner_args)
    assert run_cli(["--enabled", "--request", path, "--request-sha256", pin], capsys) == (
        2, "", '{"error":"staged_backend_mismatch"}\n')
    path, pin = write_request(tmp_path, plan, planner_args, memory_path="x")
    assert run_cli(["--enabled", "--request", path, "--request-sha256", pin], capsys) == (
        2, "", '{"error":"staged_runtime_plan_refused"}\n')
    assert run_cli(["--enabled", "--request", path, "--request-sha256", "0" * 64], capsys) == (
        2, "", '{"error":"staged_runtime_plan_refused"}\n')
    good = fixed_plan(commit)
    (tmp_path / "fixed-plan.json").unlink()
    path, pin = write_request(tmp_path, good, planner_args, schema="wd.q4-public-card-plan-request.v1")
    assert run_cli(["--enabled", "--request", path, "--request-sha256", pin], capsys) == (
        2, "", '{"error":"staged_runtime_plan_refused"}\n')
    assert run_cli(["--enabled", "--request", path], capsys)[0] == 2


def test_the_committed_helper_of_this_repository_binds_through_a_fixed_plan(tmp_path):
    root = Path(__file__).resolve().parents[2]
    head = subprocess.run([GIT, "-C", str(root), "rev-parse", "HEAD"], capture_output=True, check=True).stdout
    head = head.decode().strip()
    helper = subprocess.run([GIT, "-C", str(root), "cat-file", "blob", head + ":" + HELPER],
                            capture_output=True, check=True).stdout
    site = make_tree(tmp_path / "site", {"pkg.py": b"VALUE = 1\n"})
    model = make_tree(tmp_path / "model", {"model.safetensors": WEIGHTS})
    planner_args = dict(git_executable=GIT, repository=str(root), code_roots=["tools/wd_q4_secure_transport.py"],
                        site_root=str(site), model_root=str(model), expected_model_files=["model.safetensors"],
                        expected_weights_sha256=sha(WEIGHTS), max_bytes=256 * 1024 * 1024, timeout_seconds=300)
    result = call(fixed_plan(head, backend=hashlib.sha256(helper).hexdigest()), planner_args)
    assert result["runtime_plan"]["staged_backend"]["sha256"] == hashlib.sha256(helper).hexdigest()
    assert {HELPER, "tools/wd_q4_lane_link.py", "sandbox/mind/step2/shared_result_resolver.py"} <= set(
        result["runtime_plan"]["code_paths"])
    refused(fixed_plan(head, backend=sha(helper + b"\n")), planner_args, "staged_backend_mismatch")
