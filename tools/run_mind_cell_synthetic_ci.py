"""Run only synthetic cell witnesses with a process-local, fail-fast FAISS stub.

This is not a native FAISS, deployment, model, or production test. Invoke as
a separate Python process; inherited pytest hook settings and WD pins remain
untouched. The expected node IDs are deliberately frozen against truncation.
"""
import os
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
TEST_PATH = "sandbox/mind/step2/test_cell_verified_bytes.py"
SNAPSHOT_TEST_PATH = "sandbox/mind/step2/test_cell_snapshot_bytes.py"
_NAMES = (
    "test_cell_consumer_uses_verified_data_bytes_after_path_swap[False-meta]",
    "test_cell_consumer_uses_verified_data_bytes_after_path_swap[False-provenance]",
    "test_cell_consumer_uses_verified_data_bytes_after_path_swap[True-meta]",
    "test_cell_consumer_uses_verified_data_bytes_after_path_swap[True-provenance]",
    "test_strict_collection_failure_is_quarantined_without_private_detail",
    "test_retained_vectors_swapped_after_streaming_hash_are_refused",
    "test_retained_vectors_swapped_after_snapshot_use_verified_bytes",
    "test_missing_retained_vectors_remains_readable_without_rebuild",
    "test_index_requires_exact_flat_inner_product_before_reconstruction[l2]",
    "test_index_requires_exact_flat_inner_product_before_reconstruction[wrong_metric]",
    "test_index_requires_exact_flat_inner_product_before_reconstruction[missing_metric]",
    "test_index_requires_exact_flat_inner_product_before_reconstruction[wrapper]",
    "test_index_requires_exact_flat_inner_product_before_reconstruction[subclass]",
    "test_verified_flat_inner_product_index_is_accepted_including_empty[False]",
    "test_verified_flat_inner_product_index_is_accepted_including_empty[True]",
    "test_wrong_index_semantics_are_quarantined",
    "test_index_is_never_streamed_without_the_snapshot_budget",
    "test_index_swap_before_snapshot_is_refused_before_deserialize",
    "test_index_swap_after_snapshot_deserializes_verified_bytes_and_meta",
    "test_index_snapshot_limit_is_bounded_and_exact[0-False]",
    "test_index_snapshot_limit_is_bounded_and_exact[9-False]",
    "test_index_snapshot_limit_is_bounded_and_exact[10-True]",
    "test_missing_index_deserializer_refuses_without_path_fallback",
    "test_missing_index_at_snapshot_quarantines",
)
_SNAPSHOT_NAMES = (
    "test_snapshot_verified_bytes_are_consumed_without_reopening_paths",
    "test_snapshot_tampered_bytes_refuse_before_use[receipt]",
    "test_snapshot_tampered_bytes_refuse_before_use[index]",
    "test_snapshot_tampered_bytes_refuse_before_use[meta]",
    "test_snapshot_tampered_bytes_refuse_before_use[provenance]",
    "test_snapshot_tampered_bytes_refuse_before_use[vectors]",
    "test_snapshot_missing_required_bytes_never_falls_back[receipt]",
    "test_snapshot_missing_required_bytes_never_falls_back[index]",
    "test_snapshot_missing_required_bytes_never_falls_back[meta]",
    "test_snapshot_missing_required_bytes_never_falls_back[provenance]",
    "test_snapshot_missing_vectors_preserves_legacy_no_rebuild",
    "test_snapshot_consumer_is_default_off_and_size_bounded",
    "test_snapshot_map_mutated_during_deserialize_keeps_original_verified_bytes",
    "test_snapshot_rejects_mutable_bytes_and_unexpected_keys",
    "test_snapshot_refuses_non_topology_and_path_cell_ids_before_verification[../learning]",
    "test_snapshot_refuses_non_topology_and_path_cell_ids_before_verification[learning/..]",
    r"test_snapshot_refuses_non_topology_and_path_cell_ids_before_verification[C:\\private]",
    "test_snapshot_refuses_non_topology_and_path_cell_ids_before_verification[/absolute]",
    "test_snapshot_refuses_non_topology_and_path_cell_ids_before_verification[]",
    "test_snapshot_refuses_non_topology_and_path_cell_ids_before_verification[.]",
    "test_snapshot_refuses_non_topology_and_path_cell_ids_before_verification[unknown]",
    "test_snapshot_collection_exposes_no_mutators_or_mutable_readback",
    "test_snapshot_q4_reads_nonempty_pairs_without_io_and_reconstruct_is_defensive",
    "test_snapshot_reconstruct_budget_refuses_before_native_call",
    "test_snapshot_collection_rejects_mutable_text_values",
    "test_snapshot_reconstruction_accepts_verified_numeric_string_metadata_dim",
)
EXPECTED_NODEIDS = (tuple(TEST_PATH + "::" + name for name in _NAMES) +
                    tuple(SNAPSHOT_TEST_PATH + "::" + name for name in _SNAPSHOT_NAMES))


def _unavailable(*_args, **_kwargs):
    raise RuntimeError("synthetic_faiss_unavailable")


def _fake_faiss():
    fake = types.ModuleType("faiss")
    fake.IndexFlatIP = _unavailable
    fake.read_index = _unavailable
    fake.deserialize_index = _unavailable
    fake.METRIC_INNER_PRODUCT = 0
    return fake


class _Guard:
    def __init__(self):
        self.collection_ok = False
        self.called = set()
        self.refused = False

    def pytest_configure(self, config):
        required = ("WD_D15_HOOK_REPORT" in os.environ or
                    "hook_plugin_d15" in os.environ.get("PYTEST_ADDOPTS", ""))
        if required and not config.pluginmanager.hasplugin("hook_plugin_d15"):
            raise RuntimeError("mandatory_d15_hook_missing")

    def pytest_collectreport(self, report):
        if report.skipped or report.failed:
            self.refused = True

    def pytest_collection_finish(self, session):
        actual = [item.nodeid for item in session.items]
        self.collection_ok = len(actual) == len(EXPECTED_NODEIDS) and set(actual) == set(EXPECTED_NODEIDS)

    def pytest_runtest_logreport(self, report):
        if report.skipped or hasattr(report, "wasxfail"):
            self.refused = True
        if report.when == "call":
            self.called.add(report.nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        if (not self.collection_ok or self.refused or self.called != set(EXPECTED_NODEIDS)) and not exitstatus:
            session.exitstatus = 1


def _run_pytest(pytest):
    return int(pytest.main([str(ROOT / TEST_PATH), str(ROOT / SNAPSHOT_TEST_PATH), "-q"], plugins=[_Guard()]))


def main():
    if any(name in sys.modules for name in ("faiss", "core.faiss_store", "sandbox.mind.step2.cell")):
        return 2
    sys.path.insert(0, str(ROOT))
    sys.modules["faiss"] = _fake_faiss()
    import pytest
    return _run_pytest(pytest)


if __name__ == "__main__":
    raise SystemExit(main())
