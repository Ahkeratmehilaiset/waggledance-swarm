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
    assert runtime._remaining_owner_commit(100*mib, limits) == headroom_mib*mib - runtime._OWNER_COMMIT_RESERVE


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
