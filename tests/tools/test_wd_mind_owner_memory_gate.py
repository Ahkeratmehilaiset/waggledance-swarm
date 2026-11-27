"""Memory admission during a refresh, using an isolated synthetic owner."""
import json
import sys
from types import SimpleNamespace

import pytest

from test_wd_mind_full_runtime import synthetic_bundle
from test_wd_mind_incremental_refresh_integration import ROOT, SCRIPT, _prepared_bundle, run_owned
from tools import wd_mind_full_runtime as runtime


@pytest.mark.skipif(sys.platform != "win32", reason="Windows commit accounting")
def test_current_process_memory_is_charged():
    assert 0 <= runtime._owner_memory_headroom() < runtime._OWNER_COMMIT_LIMIT - runtime._OWNER_COMMIT_RESERVE


@pytest.mark.parametrize(("flags", "headroom_mib"), [(0, 10140), (0x100, 300), (0x200, 200), (0x300, 200)])
def test_headroom_respects_process_and_job_charges(flags, headroom_mib):
    mib = 1024**2
    limits = SimpleNamespace(basic=SimpleNamespace(flags=flags), process_memory=400*mib,
                             job_memory=1000*mib, peak_job_memory=800*mib)
    assert runtime._remaining_owner_commit(100*mib, limits, 800*mib) == headroom_mib*mib - runtime._OWNER_COMMIT_RESERVE


def test_job_charge_can_recover_after_a_peak():
    mib = 1024**2
    limits = SimpleNamespace(basic=SimpleNamespace(flags=0x200), job_memory=900*mib, peak_job_memory=899*mib)
    assert runtime._remaining_owner_commit(100*mib, limits, 899*mib) == 0
    assert runtime._remaining_owner_commit(100*mib, limits, 200*mib) == 700*mib - runtime._OWNER_COMMIT_RESERVE


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job object")
def test_current_job_charge_on_native_limited_child():
    import ctypes
    import subprocess
    from tools.wd_mind_runtime_child import _ExtendedLimit, _Job

    job = _Job()
    try:
        limit = 512 * 1024**2
        limits = _ExtendedLimit()
        limits.basic.flags = 0x200
        limits.job_memory = limit
        assert job.api.SetInformationJobObject(job.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
        code = ("import sys; from tools.wd_mind_full_runtime import _owner_memory_headroom; "
                "sys.stdin.readline(); print(_owner_memory_headroom())")
        # The Windows venv redirector starts another process before it can
        # be assigned. Put the interpreter that samples the job in it directly.
        with subprocess.Popen([sys._base_executable, "-B", "-c", code], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as proc:
            try:
                job.attach(proc)
                out, err = proc.communicate("sample\n", timeout=30)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            assert proc.returncode == 0, err
            assert 0 < int(out) < limit - runtime._OWNER_COMMIT_RESERVE
    finally:
        job.close()


def test_startup_reports_unavailable_meter(tmp_path, monkeypatch):
    from test_wd_mind_owner_diagnostics import _local_adapter
    from tools import wd_mind_runtime_child as child

    def fail():
        raise OSError("synthetic meter fault")

    monkeypatch.setattr(runtime, "_owner_memory_headroom", fail)
    monkeypatch.setattr(child._LocalManifestQuery, "_check_policy", lambda self: None)
    monkeypatch.setattr(child._LocalManifestQuery, "_check_external_policy", lambda self: None)
    monkeypatch.setattr(runtime, "_bundle_inputs", lambda *args: ())
    monkeypatch.setattr(runtime.reader, "_load_identifier_policy", lambda *args, **kwargs: {})
    adapter = _local_adapter()
    adapter._bundle = str(tmp_path)
    adapter._policy_sha = runtime.reader._sha(adapter._policy)
    try:
        adapter.start()
        assert adapter.wait(timeout=5)["state"] == "HOLD"
        assert adapter.start_diagnostic()["reason"] == "OWNER_METER_UNAVAILABLE"
    finally:
        adapter.close()


@pytest.mark.parametrize("case", ["prefix_limit", "after_build", "after_prewarm", "meter_error"])
def test_memory_refusal_keeps_previous_index_serving(synthetic_bundle, case):
    digest, policy_sha = _prepared_bundle(synthetic_bundle)
    source = synthetic_bundle["source"]
    source.write_bytes(b"\n".join(source.read_bytes().splitlines()[:5]) + b"\n")
    marker = "    if case == 'busy':\n"
    assert SCRIPT.count(marker) == 1
    script = SCRIPT.replace(marker, """    if case in ('prefix_limit', 'after_build', 'after_prewarm', 'meter_error'):
        original = runtime._owner_memory_headroom
        built = []
        original_build = runtime._FlatRequestHandler._build
        def record_build(handler, *args):
            result = original_build(handler, *args)
            built.append(True)
            return result
        def measure():
            if case == 'meter_error':
                raise OSError('synthetic measurement error')
            if case == 'prefix_limit':
                return source.stat().st_size * runtime.reader._EAGER_MEMORY_BYTES_PER_SOURCE_BYTE - 1
            if case == 'after_prewarm':
                return 0 if owner._generation is not generation else 1024**3
            return 0 if built else 1024**3
        runtime._owner_memory_headroom = measure
        runtime._FlatRequestHandler._build = record_build
        try:
            try: owner._refresh_generation(enabled=True)
            except runtime.FullRuntimeRefused as exc:
                assert str(exc) == 'OWNER_MEMORY_BUDGET', str(exc)
            else: raise AssertionError('memory refusal expected')
        finally:
            runtime._owner_memory_headroom = original
            runtime._FlatRequestHandler._build = original_build
        assert built == ([True] if case in ('after_build', 'after_prewarm') else [])
        assert owner._generation is generation and owner.status()['state'] == 'READY'
        assert owner.query_local(enabled=True, lane='codex-lead-1', scope='own',
                                 query='current mission overview')['results']
    elif case == 'busy':
""")
    result = run_owned([sys.executable, "-B", "-c", script, str(synthetic_bundle["bundle"]),
                        digest, policy_sha, str(ROOT / "tests/tools"), str(source), case],
                       capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"status": "ok", "case": case}
