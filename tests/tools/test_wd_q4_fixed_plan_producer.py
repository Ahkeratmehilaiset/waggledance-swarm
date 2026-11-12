"""Synthetic pins only: the default-off ``wd.q4-fixed-plan.v2`` producer against the existing parser.

No file, interpreter, runtime tree, model, bundle or peer is read or spawned. Every pin is a caller
argument. The parser under test is ``tools.wd_q4_secure_transport._fixed_plan``; on POSIX tests
its module-local path operations simulate Windows. This does not prove a POSIX runtime path.
"""
import builtins
import copy
import hashlib
import inspect
import json
import ntpath
import os
import posixpath
import sys
import types

import pytest

from tools import wd_q4_secure_transport as transport


class _WindowsPathOs:
    """Delegate OS operations while scoping Windows path semantics to this module."""

    path = ntpath

    def __getattr__(self, name):
        return getattr(os, name)


@pytest.fixture(autouse=True)
def _windows_parser_paths(monkeypatch):
    # The producer and staged transport consume Windows C: paths. Model the
    # Windows parser on POSIX CI without replacing the process-wide os.path.
    # On Windows, exercise the native parser unchanged.
    if os.name != "nt":
        monkeypatch.setattr(transport, "os", _WindowsPathOs())


def test_windows_path_simulation_is_scoped_to_transport(monkeypatch):
    original_os_path = os.path
    # The Windows-only producer yields a C: spelling, which POSIX path rules
    # correctly refuse. This proxy never changes the real process os.path.
    monkeypatch.setattr(transport, "os", types.SimpleNamespace(path=posixpath))
    with pytest.raises(transport._Refused):
        transport._fixed_plan(build()["plan_bytes"])
    monkeypatch.setattr(transport, "os", _WindowsPathOs())
    assert transport.os.path is ntpath
    assert os.path is original_os_path
    assert transport._fixed_plan(build()["plan_bytes"])["runtime_root"] == ROOT


def test_posix_fixture_installs_only_the_transport_path_proxy(monkeypatch):
    native_os = os
    native_path = native_os.path
    # Drive the POSIX fixture branch even when this regression runs on Windows.
    monkeypatch.setattr(sys.modules[__name__], "os", types.SimpleNamespace(name="posix"))
    _windows_parser_paths.__wrapped__(monkeypatch)
    assert transport.os.path is ntpath
    assert native_os.path is native_path
    assert transport._fixed_plan(build()["plan_bytes"])["runtime_root"] == ROOT


def test_content_v4_fixed_backend_preserves_exact_seven_public_pins():
    """The content backend must be explicit and cannot fall through to legacy pointer mode."""
    from tools import wd_q4_fixed_plan_producer as producer

    expected = {name: format(i + 1, "x") * 64 for i, name in enumerate((
        "encoder_sha256", "policy_sha256", "roster_sha256", "content_decision_sha256",
        "descriptor_sha256", "memory_sha256", "projection_sha256"))}
    result = producer.build_content_fixed_plan(
        enabled=True,
        **expected,
        interpreter_sha256="a" * 64,
        peer_code_sha256="b" * 64,
        runtime_manifest_sha256="c" * 64,
        bundle_sha256="d" * 64,
        verifier_sha256="e" * 64,
        wrapper_sha256="f" * 64,
        deployment_manifest_sha256="1" * 64,
        public_plan_sha256="2" * 64,
        host_sha256="3" * 64,
        backend_sha256="4" * 64,
        bundle_bytes=100,
        runtime_root=r"C:\synthetic\runtime",
        runtime_manifest_path=r"C:\synthetic\runtime-manifest.json",
        runtime_generation="a" * 40,
        runtime_revision="b" * 40,
        bundle_root=r"C:\synthetic\bundle",
        bundle_commit="c" * 40,
        wrapper_path=r"C:\synthetic\bundle\Invoke-WdQ4Query.ps1",
        public_plan_path=r"C:\synthetic\public-plan.json",
        approved_tasks=["synthetic-task"],
    )
    plan = transport._fixed_plan(result["plan_bytes"])
    assert plan["schema"] == "wd.q4-fixed-plan.v4"
    assert plan["backend"] == "content-wrapper-v1"
    assert {name: plan[name] for name in expected} == expected
    assert "expected_pointer_sha256" not in plan


@pytest.mark.parametrize("missing", sorted(transport._CONTENT_PINS))
def test_content_v4_fixed_parser_rejects_each_missing_public_pin(missing):
    document = {
        "schema": "wd.q4-fixed-plan.v4", "backend": "content-wrapper-v1",
        **{key: "a" * 64 for key in transport._CONTENT_FIXED_KEYS
           if key.endswith("_sha256")},
        "bundle_bytes": 100, "runtime_root": r"C:\synthetic\runtime",
        "runtime_manifest_path": r"C:\synthetic\runtime-manifest.json",
        "runtime_generation": "a" * 40, "runtime_revision": "b" * 40,
        "approved_tasks": ["synthetic-task"], "bundle_root": r"C:\synthetic\bundle",
        "bundle_commit": "c" * 40, "wrapper_path": r"C:\synthetic\bundle\Invoke-WdQ4Query.ps1",
        "public_plan_path": r"C:\synthetic\public-plan.json",
    }
    assert transport._fixed_plan(transport._canonical(document)) == document
    del document[missing]
    with pytest.raises(transport._Refused):
        transport._fixed_plan(transport._canonical(document))

SCHEMA = "wd.q4-fixed-plan.v2"
BACKEND = "public-snapshot-v1"
PINS = ("interpreter_sha256", "peer_code_sha256", "verifier_sha256", "runtime_manifest_sha256", "bundle_sha256",
        "expected_pointer_sha256", "policy_sha256", "roster_sha256", "expected_public_policy_sha256",
        "expected_source_sha256", "expected_encoder_sha256")
IDENTITIES = ("runtime_generation", "runtime_revision")
PATHS = ("runtime_root", "runtime_manifest_path")
REASONS = {"disabled", "pin_refused", "path_refused", "limit_refused", "tasks_refused", "schema_refused"}
SENTINEL = "sentinelvalue7f3a"
ROOT = r"C:\synthetic\q4\runtime-root"
MANIFEST = r"C:\synthetic\q4\runtime-manifest.json"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def module():
    from tools import wd_q4_fixed_plan_producer
    return wd_q4_fixed_plan_producer


def kwargs(**changes):
    out = {name: format(index + 1, "x") * 64 for index, name in enumerate(PINS)}
    out.update(runtime_generation="a" * 40, runtime_revision="b" * 64, runtime_root=ROOT,
               runtime_manifest_path=MANIFEST, bundle_bytes=102400, approved_tasks=["task-a", "task-b"])
    out.update(changes)
    return out


def build(**changes):
    return module().build_fixed_plan(enabled=True, **kwargs(**changes))


def refused(reason, **changes):
    m = module()
    with pytest.raises(m.FixedPlanRefused) as caught:
        build(**changes)
    assert caught.value.args == (reason,) and reason in REASONS
    assert vars(caught.value) == {} and caught.value.__cause__ is None
    assert SENTINEL not in repr(caught.value)
    return caught.value


# 1 off by default and an explicit keyword-only surface ---------------------------------------------------------


def test_disabled_is_the_default_and_refuses_before_validating_anything():
    m = module()
    with pytest.raises(m.FixedPlanRefused) as caught:
        m.build_fixed_plan(**kwargs(interpreter_sha256=SENTINEL))
    assert caught.value.args == ("disabled",)
    with pytest.raises(m.FixedPlanRefused) as caught:
        m.build_fixed_plan(enabled=False, **kwargs())
    assert caught.value.args == ("disabled",)


@pytest.mark.parametrize("enabled", [1, "true", "1", [True], None])
def test_only_the_exact_true_enables(enabled):
    m = module()
    with pytest.raises(m.FixedPlanRefused) as caught:
        m.build_fixed_plan(enabled=enabled, **kwargs())
    assert caught.value.args == ("disabled",)


def test_the_surface_is_keyword_only_explicit_pins_without_schema_backend_or_query():
    m = module()
    signature = inspect.signature(m.build_fixed_plan)
    names = set(signature.parameters)
    assert names == {"enabled", "bundle_bytes", "approved_tasks", *PINS, *IDENTITIES, *PATHS}
    for name, parameter in signature.parameters.items():
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert (parameter.default is False) if name == "enabled" else (parameter.default is inspect.Parameter.empty)
    for extra in ("schema", "backend", "query", "situation", "time"):
        with pytest.raises(TypeError):
            m.build_fixed_plan(enabled=True, **kwargs(), **{extra: "x"})
    with pytest.raises(TypeError):
        m.build_fixed_plan(True, **kwargs())
    for name in kwargs():
        missing = kwargs()
        del missing[name]
        with pytest.raises(TypeError):
            m.build_fixed_plan(enabled=True, **missing)


def test_module_constants_mirror_the_parser():
    m = module()
    assert m.SCHEMA == SCHEMA and m.BACKEND == BACKEND
    assert transport._PLAN_SCHEMAS[SCHEMA] == (BACKEND, transport._PUBLIC_PLAN_KEYS)
    assert m.MAX_PLAN_BYTES == transport._MAX_PLAN == 4096
    assert m.MAX_BUNDLE_BYTES == transport._MAX_BUNDLE == 16 * 1024 * 1024
    assert set(m.PINS) == set(PINS) and set(m.PINS) == (
        set(transport._PUBLIC_PINS) | {"interpreter_sha256", "peer_code_sha256", "verifier_sha256",
                                        "runtime_manifest_sha256", "bundle_sha256"})
    assert m.REASONS == frozenset(REASONS)


# 2 the produced bytes are what the parent and the child parse ---------------------------------------------------


def test_the_existing_parser_accepts_the_produced_plan_byte_for_byte():
    result = build()
    assert set(result) == {"plan_bytes", "plan_sha256"}
    raw = result["plan_bytes"]
    assert type(raw) is bytes and result["plan_sha256"] == sha(raw)
    parsed = transport._fixed_plan(raw)
    assert set(parsed) == transport._PUBLIC_PLAN_KEYS
    assert parsed["schema"] == SCHEMA and parsed["backend"] == BACKEND
    assert transport._canonical(parsed) == raw
    for name in (*PINS, *IDENTITIES, *PATHS, "bundle_bytes", "approved_tasks"):
        assert parsed[name] == kwargs()[name]


def test_the_plan_digest_is_the_binding_the_parent_checks_against_the_caller_pin():
    result = build()
    assert transport._sha(result["plan_bytes"]) == result["plan_sha256"]
    assert transport._hex64(result["plan_sha256"]) == result["plan_sha256"]


def test_output_is_deterministic_canonical_ascii_json_sorted_without_whitespace():
    first, second = build(), build()
    assert first == second
    raw = first["plan_bytes"]
    assert raw.isascii() and b" " not in raw and b"\n" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    document = json.loads(raw)
    assert list(document) == sorted(document)
    assert raw == json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def test_a_changed_byte_no_longer_matches_the_returned_pin():
    result = build()
    raw = bytearray(result["plan_bytes"])
    index = raw.index(b"task-a") + 5
    raw[index] = ord("z")
    assert sha(bytes(raw)) != result["plan_sha256"]
    # task-z before task-b is no longer sorted, so the parser refuses the altered bytes as well
    with pytest.raises(transport._Refused):
        transport._fixed_plan(bytes(raw))


def test_the_caller_inputs_are_not_mutated_and_a_later_change_does_not_leak_in():
    kw = kwargs()
    frozen = copy.deepcopy(kw)
    result = module().build_fixed_plan(enabled=True, **kw)
    assert kw == frozen
    kw["approved_tasks"].append("task-c")
    assert transport._fixed_plan(result["plan_bytes"])["approved_tasks"] == ["task-a", "task-b"]


def test_no_file_process_or_environment_is_touched(monkeypatch):
    module()

    def blocked(*args, **kwargs):
        raise AssertionError("side effect")

    for owner, name in ((builtins, "open"), (os, "open"), (os, "stat"), (os, "lstat"), (os, "scandir"),
                        (os, "listdir"), (os, "getcwd")):
        monkeypatch.setattr(owner, name, blocked)
    monkeypatch.setattr(hashlib, "file_digest", blocked, raising=False)
    result = build()
    monkeypatch.undo()
    assert result["plan_sha256"] == sha(result["plan_bytes"])
    source = inspect.getsource(module())
    for token in ("subprocess", "os.environ", "import sys", "import os", "open(", "Path(", "urandom", "datetime",
                  "time."):
        assert token not in source, token
    assert not hasattr(module(), "sys") and not hasattr(module(), "os")


# 3 refusals: one closed reason, never a caller value ------------------------------------------------------------


BAD_PINS = ["A" * 64, "a" * 63, "a" * 65, "g" * 64, "", b"a" * 64, None, 1, ["a" * 64], SENTINEL + "a" * 48]


@pytest.mark.parametrize("name", PINS)
@pytest.mark.parametrize("value", BAD_PINS, ids=[f"bad{i}" for i in range(len(BAD_PINS))])
def test_every_pin_must_be_64_lowercase_hex(name, value):
    refused("pin_refused", **{name: value})


@pytest.mark.parametrize("name", IDENTITIES)
@pytest.mark.parametrize("value", ["a" * 39, "a" * 41, "a" * 63, "a" * 65, "A" * 40, "g" * 64, "", None, 40,
                                   b"a" * 40, SENTINEL + "a" * 24])
def test_runtime_generation_and_revision_must_be_40_or_64_lowercase_hex(name, value):
    refused("pin_refused", **{name: value})


@pytest.mark.parametrize("length", [40, 64])
def test_both_identity_lengths_are_accepted(length):
    parsed = transport._fixed_plan(build(runtime_generation="c" * length, runtime_revision="d" * length)["plan_bytes"])
    assert parsed["runtime_generation"] == "c" * length and parsed["runtime_revision"] == "d" * length


@pytest.mark.parametrize("name", PATHS)
@pytest.mark.parametrize("value", ["", "relative\\path", "C:relative", "C:\\" + "x" * 510, "C:\\s\u00fcn", "C:\\a\tb",
                                   "C:\\a\x00b", None, b"C:\\x", 7, [ROOT], "\\\\server\\share\\x"])
def test_paths_follow_the_parser_rules_absolute_ascii_printable_at_most_512(name, value):
    refused("path_refused", **{name: value})


@pytest.mark.parametrize("manifest", [ROOT, ROOT + "\\", ROOT + "\\manifest.json", ROOT + "\\deep\\manifest.json",
                                      ROOT.upper() + "\\manifest.json", ROOT.replace("\\", "/") + "/manifest.json",
                                      ROOT.lower()])
def test_the_manifest_may_not_live_at_or_inside_the_runtime_root(manifest):
    """runtime_manifest.verify_runtime refuses manifest_inside_tree by casefolded commonpath."""
    refused("path_refused", runtime_manifest_path=manifest)


@pytest.mark.parametrize("manifest", ["D:\\synthetic\\q4\\runtime-manifest.json", "\\synthetic\\manifest.json"])
def test_the_manifest_must_share_the_runtime_root_drive(manifest):
    """verify_runtime's os.path.commonpath raises for different drives and the verifier refuses root_invalid."""
    refused("path_refused", runtime_manifest_path=manifest)


def test_a_manifest_at_the_drive_root_is_outside_a_deeper_runtime_root():
    parsed = transport._fixed_plan(build(runtime_manifest_path="C:\\")["plan_bytes"])
    assert parsed["runtime_manifest_path"] == "C:\\"
    refused("path_refused", runtime_root="C:\\", runtime_manifest_path="C:\\manifest.json")


@pytest.mark.parametrize("name", PATHS)
@pytest.mark.parametrize("value", ["C:\\work..\\m.json", "C:\\work.\\m.json", "C:\\work \\m.json", "C:\\work\\m.json ",
                                   "C:\\work\\m.json.", "C:\\work:secret", "C:\\work\\m:stream", "C:\\a\\..\\m.json",
                                   "C:\\a\\.\\m.json", "C:\\a\\\\m.json", "C:\\CON\\m.json", "C:\\a\\nul.txt",
                                   "C:\\a\\COM1", "C:/synthetic/m.json", "C:\\synthetic/m.json"])
def test_parts_the_verifier_chain_check_or_the_descriptor_validator_refuse_are_refused_before_emit(name, value):
    """runtime_manifest._check_chain refuses a part with ':' or a trailing '.'/' ' or a device name (Win32 would
    strip the trailing character and open a different path); dot parts and forward slashes are refused as the
    shared descriptor validator refuses them."""
    refused("path_refused", **{name: value})


def test_inner_dots_and_spaces_in_a_part_are_fine():
    parsed = transport._fixed_plan(build(runtime_manifest_path="C:\\synthetic\\q4 .v2\\runtime-manifest.json")["plan_bytes"])
    assert parsed["runtime_manifest_path"] == "C:\\synthetic\\q4 .v2\\runtime-manifest.json"


def test_a_sibling_whose_name_merely_extends_the_root_is_outside_it():
    parsed = transport._fixed_plan(build(runtime_manifest_path=ROOT + "-manifest.json")["plan_bytes"])
    assert parsed["runtime_manifest_path"] == ROOT + "-manifest.json"


@pytest.mark.parametrize("value", [0, -1, 16 * 1024 * 1024 + 1, True, False, 1.0, "1", None, 2 ** 63])
def test_bundle_bytes_is_a_bounded_positive_int(value):
    refused("limit_refused", bundle_bytes=value)


@pytest.mark.parametrize("value", [1, 16 * 1024 * 1024])
def test_bundle_bytes_bounds_are_inclusive(value):
    assert transport._fixed_plan(build(bundle_bytes=value)["plan_bytes"])["bundle_bytes"] == value


@pytest.mark.parametrize("tasks", [[], ["b", "a"], ["a", "a"], [""], ["x" * 129], ["t\u00e4sk"], ["a\tb"], [1],
                                   [None], ("a", "b"), "ab", None, [f"t{i:03d}" for i in range(65)],
                                   ["a", SENTINEL, "a"]])
def test_approved_tasks_is_a_sorted_unique_bounded_list_and_is_never_normalised_for_the_caller(tasks):
    refused("tasks_refused", approved_tasks=tasks)


def test_sixty_four_short_tasks_are_accepted_and_the_order_is_preserved():
    tasks = [f"t{i:03d}" for i in range(64)]
    assert transport._fixed_plan(build(approved_tasks=tasks)["plan_bytes"])["approved_tasks"] == tasks


def test_a_plan_over_the_parser_frame_limit_is_refused_not_truncated():
    tasks = sorted({f"{i:03d}" + "x" * 125 for i in range(32)})
    refused("limit_refused", approved_tasks=tasks)
    tasks = sorted({f"{i:03d}" + "x" * 60 for i in range(32)})
    assert len(build(approved_tasks=tasks)["plan_bytes"]) <= 4096


@pytest.mark.parametrize("reason, change", [
    ("pin_refused", {"interpreter_sha256": SENTINEL + "a" * 48}),
    ("pin_refused", {"runtime_revision": SENTINEL + "a" * 24}),
    ("path_refused", {"runtime_root": "relative\\" + SENTINEL}),
    ("path_refused", {"runtime_manifest_path": ROOT + "\\" + SENTINEL}),
    ("tasks_refused", {"approved_tasks": [SENTINEL, "a"]}),
    ("limit_refused", {"bundle_bytes": 0}),
])
def test_a_refusal_never_carries_the_caller_value(reason, change):
    exc = refused(reason, **change)
    assert str(exc) == reason and SENTINEL not in str(exc.__traceback__)


# 4 the consumer side: default-off transport refuses the produced plan before any spawn -------------------------


def test_the_transport_refuses_the_produced_plan_unless_public_snapshot_is_explicitly_enabled(tmp_path, monkeypatch):
    import subprocess

    def blocked(*args, **kwargs):
        raise AssertionError("spawn")

    monkeypatch.setattr(subprocess, "Popen", blocked)
    result = build(interpreter_sha256="1" * 64)
    with pytest.raises(transport._Refused):
        transport.run_fixed_peer(executable=sys.executable, expected_interpreter_sha256="1" * 64,
                                 interpreter_max_bytes=64 * 1024 * 1024, plan_bytes=result["plan_bytes"],
                                 expected_plan_sha256=result["plan_sha256"], bundle_bytes=b"x" * 102400,
                                 runtime_manifest_bytes=b"{}", query_bytes=b'{"situation":"s","time":"t"}',
                                 cwd=str(tmp_path), timeout_seconds=60, max_output_bytes=4096)
    assert not transport._QUARANTINE


# 5 documentation ------------------------------------------------------------------------------------------------


def test_documentation_states_the_boundaries():
    doc = module().__doc__
    for phrase in ("default-off", "no I/O", "explicit caller argument", "not authentication", "not freshness",
                   "not a runtime proof", "sys.executable", "wd_q4_secure_transport.py", "runtime_manifest.py",
                   "approved_tasks", "outside the runtime root", "generation", "revision", "closed reason",
                   "never a caller value", "canonical ASCII JSON", "4096", "forward slash", "8.3", "frame locals"):
        assert phrase in doc, phrase
