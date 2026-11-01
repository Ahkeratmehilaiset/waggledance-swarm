"""Run only synthetic cell witnesses with a process-local, fail-fast FAISS stub.

This is not a native FAISS, deployment, model, or production test. The snapshot
checkpoint API always refuses; its refusal tests do not demonstrate a working
public API or prove each semantic mismatch was detected. Invoke as
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
DESCRIPTOR_TEST_PATH = "sandbox/mind/step2/test_wd_q4_private_read_bytes.py"
MEMORY_TEST_PATH = "sandbox/mind/step2/test_memory_snapshot.py"
VERIFIED_TEST_PATH = "sandbox/mind/step2/test_verified_snapshot_checkpoint.py"
Q4_DESCRIPTOR_TEST_PATH = "sandbox/mind/step2/test_q4_descriptor.py"
ACTIVE_BINDING_TEST_PATH = "sandbox/mind/step2/test_verified_snapshot_active_binding.py"
PROJECTION_BINDING_TEST_PATH = "sandbox/mind/step2/test_verified_snapshot_projection_binding.py"
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
    "test_snapshot_refuses_numeric_string_receipt_dim",
    "test_snapshot_still_accepts_numeric_string_meta_dim_when_receipt_dim_is_integer",
)
_DESCRIPTOR_NAMES = (
    "test_descriptor_bytes_match_existing_path_without_path_io",
    "test_descriptor_bytes_refusals_match_path_api[bad_pin-descriptor_pin_invalid]",
    "test_descriptor_bytes_refusals_match_path_api[wrong_pin-descriptor_pin_mismatch]",
    "test_descriptor_bytes_refusals_match_path_api[too_large-descriptor_over_budget]",
    "test_descriptor_bytes_refusals_match_path_api[invalid_json-descriptor_unreadable]",
    "test_descriptor_bytes_refusals_match_path_api[invalid_scope-descriptor_scope_mismatch]",
    "test_descriptor_bytes_refusals_match_path_api[invalid_shape-descriptor_shape]",
    "test_descriptor_bytes_refuse_mutable_input",
    "test_descriptor_exact_budget_is_accepted_and_pin_precedes_read",
    'test_malformed_descriptor_bytes_do_not_retain_json_exception[{"private-sentinel":"unterminated]',
    r"test_malformed_descriptor_bytes_do_not_retain_json_exception[\xff]",
)
_MEMORY_NAMES = (
    "test_snapshot_memory_answers_nonempty_q4_without_path_io",
    "test_snapshot_memory_refuses_missing_extra_or_unbound_data[missing_cell]",
    "test_snapshot_memory_refuses_missing_extra_or_unbound_data[extra_cell]",
    "test_snapshot_memory_refuses_missing_extra_or_unbound_data[bad_log]",
    "test_snapshot_memory_refuses_missing_extra_or_unbound_data[wrong_receipt]",
    "test_snapshot_memory_refuses_missing_extra_or_unbound_data[wrong_generation]",
    "test_snapshot_memory_refuses_missing_extra_or_unbound_data[wrong_dim]",
    "test_snapshot_memory_refuses_missing_extra_or_unbound_data[wrong_pin]",
    "test_snapshot_memory_refuses_missing_extra_or_unbound_data[unknown_manifest_cell]",
    "test_snapshot_memory_refuses_missing_extra_or_unbound_data[extra_snapshot_field]",
    "test_snapshot_memory_is_default_off",
    "test_snapshot_memory_refuses_binding_and_budget_before_native_deserialization[wrong_receipt]",
    "test_snapshot_memory_refuses_binding_and_budget_before_native_deserialization[wrong_generation]",
    "test_snapshot_memory_refuses_binding_and_budget_before_native_deserialization[bad_log]",
    "test_snapshot_memory_refuses_binding_and_budget_before_native_deserialization[aggregate_budget]",
    "test_snapshot_control_and_artifact_limits_fail_closed[missing_log]",
    "test_snapshot_control_and_artifact_limits_fail_closed[extra_artifact]",
    "test_snapshot_control_and_artifact_limits_fail_closed[mutable_pointer]",
    "test_snapshot_control_and_artifact_limits_fail_closed[wrong_sequence]",
    "test_snapshot_control_and_artifact_limits_fail_closed[oversize_pointer]",
    "test_snapshot_control_and_artifact_limits_fail_closed[oversize_log]",
    "test_snapshot_memory_rejects_numeric_string_receipt_dim",
    "test_snapshot_memory_checks_entire_two_entry_log_chain",
    "test_snapshot_rejects_boolean_log_numbers_even_in_prior_lines[first-seq]",
    "test_snapshot_rejects_boolean_log_numbers_even_in_prior_lines[first-generation]",
    "test_snapshot_rejects_boolean_log_numbers_even_in_prior_lines[last-generation]",
    "test_malformed_snapshot_refusal_does_not_chain_raw_input",
    "test_snapshot_memory_aggregate_limit_covers_all_cells_before_native_call",
    "test_all_cells_control_preflight_before_any_native_deserialization[pointer]",
    "test_all_cells_control_preflight_before_any_native_deserialization[log]",
    "test_all_cells_control_preflight_before_any_native_deserialization[receipt_hash]",
    "test_all_cells_control_preflight_before_any_native_deserialization[receipt_dim]",
)
_VERIFIED_NAMES = (
    "test_public_preflight_no_io_but_checkpoint_stays_fail_closed",
    *(f"test_snapshot_rejects_general_nonreview_data_not_approved_for_public[{v}]" for v in
      ("private_task", "private_type")),
    *(f"test_public_preflight_rejects_untrusted_pins_and_policy[{v}]" for v in
      ("missing_source", "wrong_source", "mutable_source", "missing_encoder", "wrong_encoder",
       "mutable_encoder", "wrong_policy_hash", "malformed_policy", "missing_policy", "mutable_policy")),
    "test_public_preflight_honors_narrowed_agent_allowlist",
    "test_even_valid_public_input_refuses_before_any_native_call",
    *(f"test_refusal_frame_locals_do_not_retain_invalid_private_inputs[{target}-{field}]"
      for target, field in (
          ("read_verified_snapshot_checkpoint", "expected_source_sha256"),
          ("read_verified_snapshot_checkpoint", "expected_encoder_sha256"),
          ("read_verified_snapshot_checkpoint", "enabled"),
          ("_preflight_public_snapshot", "expected_source_sha256"),
          ("_preflight_public_snapshot", "expected_encoder_sha256"),
          ("_preflight_public_snapshot", "expected_public_policy_sha256"))),
    *(f"test_snapshot_never_returns_memory_with_private_unchecked_fields[{v}]" for v in
      ("row_extra_private", "metadata_extra_private", "receipt_extra_private")),
    *(f"test_snapshot_checkpoint_refuses_rehashed_semantic_mismatch[{v}]" for v in
      ("policy", "encoder", "row_kind", "text", "wrong_event_ref", "duplicate_view", "missing_view",
       "source_binding", "source_mode", "receipt_dim", "review_projection", "projection_schema")),
    *(f"test_snapshot_checkpoint_refuses_tamper_or_missing_pin[{v}]" for v in
      ("pointer", "artifact", "cell_log", "policy", "roster", "history", "archive_missing", "disabled")),
    *(f"test_invalid_active_controls_refuse_before_faiss[{v}]" for v in
      ("policy", "source_binding", "source_mode", "receipt_dim", "review_projection", "projection_schema")),
    "test_snapshot_checkpoint_respects_aggregate_budget_before_faiss",
    "test_malformed_snapshot_pointer_refusal_has_no_raw_exception_context",
    "test_snapshot_checkpoint_verifies_bound_prefix_history_without_disk[False]",
    "test_snapshot_checkpoint_verifies_bound_prefix_history_without_disk[True]",
)
_Q4_DESCRIPTOR_NAMES = (
    *(f"test_descriptor_refusals_preserve_existing_reason_and_exception[{fault}-{reason}]"
      for fault, reason in (("pin", "descriptor_pin_invalid"), ("bytes", "descriptor_unreadable"),
                            ("budget", "descriptor_over_budget"), ("digest", "descriptor_pin_mismatch"),
                            ("json", "descriptor_unreadable"), ("scope", "descriptor_scope_mismatch"),
                            ("shape", "descriptor_shape"))),
    "test_descriptor_valid_roundtrip_without_native",
    *(f"test_refusal_does_not_retain_raw_payload_or_cause[{kind}]"
      for kind in ("invalid_pin", "mismatch", "malformed", "shape")),
    "test_unexpected_pin_error_is_scrubbed_and_fixed",
)
_ACTIVE_BINDING_NAMES = (
    *(f"test_bytes_only_active_validation_preserves_semantics_without_io_or_native[{fault}]"
      for fault in ("None", "receipt_dim", "projection_schema", "encoder", "memory_policy", "receipt_policy", "memory_name")),
    *(f"test_public_preflight_checks_binding_without_native_or_disk[{fault}]"
      for fault in ("None", "source_binding", "source_mode", "encoder_pin", "public_policy")),
    *(f"test_legacy_verifier_keeps_separate_descriptor_reread[{changed}]" for changed in ("False", "True")),
    "test_active_validation_refusal_does_not_retain_private_input",
)
_PROJECTION_BINDING_NAMES = (
    *(f"test_projection_binding_rejects_mismatch[{change}]" for change in
      ("missing", "duplicate", "extra", "text_swap", "time", "available", "event_ref", "extra_text")),
    "test_projection_binding_accepts_exact_views_across_cells",
)
EXPECTED_NODEIDS = (tuple(TEST_PATH + "::" + name for name in _NAMES) +
                    tuple(SNAPSHOT_TEST_PATH + "::" + name for name in _SNAPSHOT_NAMES) +
                    tuple(DESCRIPTOR_TEST_PATH + "::" + name for name in _DESCRIPTOR_NAMES) +
                    tuple(MEMORY_TEST_PATH + "::" + name for name in _MEMORY_NAMES) +
                    tuple(VERIFIED_TEST_PATH + "::" + name for name in _VERIFIED_NAMES) +
                    tuple(Q4_DESCRIPTOR_TEST_PATH + "::" + name for name in _Q4_DESCRIPTOR_NAMES) +
                    tuple(ACTIVE_BINDING_TEST_PATH + "::" + name for name in _ACTIVE_BINDING_NAMES) +
                    tuple(PROJECTION_BINDING_TEST_PATH + "::" + name for name in _PROJECTION_BINDING_NAMES))


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
        if report.skipped or getattr(report, "failed", False) or hasattr(report, "wasxfail"):
            self.refused = True
        if report.when == "call":
            self.called.add(report.nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        if not self.complete() and not exitstatus:
            session.exitstatus = 1

    def complete(self):
        return self.collection_ok and not self.refused and self.called == set(EXPECTED_NODEIDS)


def _run_pytest(pytest):
    guard = _Guard()
    code = int(pytest.main([str(ROOT / TEST_PATH), str(ROOT / SNAPSHOT_TEST_PATH),
                            str(ROOT / DESCRIPTOR_TEST_PATH), str(ROOT / MEMORY_TEST_PATH),
                            str(ROOT / VERIFIED_TEST_PATH), str(ROOT / Q4_DESCRIPTOR_TEST_PATH),
                            str(ROOT / ACTIVE_BINDING_TEST_PATH), str(ROOT / PROJECTION_BINDING_TEST_PATH),
                            "-q"], plugins=[guard]))
    return 1 if code == 0 and not guard.complete() else code


def main():
    if any(name in sys.modules for name in ("faiss", "core.faiss_store", "sandbox.mind.step2.cell")):
        return 2
    sys.path.insert(0, str(ROOT))
    sys.modules["faiss"] = _fake_faiss()
    print("Synthetic FAISS only; snapshot checkpoint always refuses: no public API or semantic-coverage proof.")
    import pytest
    return _run_pytest(pytest)


if __name__ == "__main__":
    raise SystemExit(main())
