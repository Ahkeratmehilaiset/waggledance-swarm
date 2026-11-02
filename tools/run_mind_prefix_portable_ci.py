"""Run a frozen set of portable prefix refusal witnesses in a fresh process.

These synthetic tests do not exercise the Windows-only success path, native
FAISS, model execution, installation, or production. Nothing in this runner
substitutes an optional dependency. The caller must retain inherited pytest
hook settings and WD pins; this runner never edits the environment.
"""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TEST_PATH = "sandbox/mind/step2/test_wd_q4_prefix_build_child.py"
_GROUPS = (
    "test_default_off_inspects_nothing",
    "test_enabled_must_be_the_bool_true",
    "test_no_encoder_callback_or_identity_can_be_supplied_by_the_caller",
    "test_plan_pin_mismatch_refuses_before_parsing",
    "test_plan_bytes_and_parser_details_refuse",
    "test_plan_over_the_byte_limit_refuses",
    "test_inference_uses_offline_environment_then_restores_real_environment",
)
SELECTORS = tuple(TEST_PATH + "::" + name for name in _GROUPS)
_NAMES = (
    "test_default_off_inspects_nothing",
    "test_enabled_must_be_the_bool_true[1]",
    "test_enabled_must_be_the_bool_true[0]",
    "test_enabled_must_be_the_bool_true[true]",
    "test_enabled_must_be_the_bool_true[None]",
    "test_enabled_must_be_the_bool_true[literal4]",
    "test_enabled_must_be_the_bool_true[1.0]",
    "test_no_encoder_callback_or_identity_can_be_supplied_by_the_caller",
    "test_plan_pin_mismatch_refuses_before_parsing",
    "test_plan_bytes_and_parser_details_refuse[None]",
    "test_plan_bytes_and_parser_details_refuse[text]",
    "test_plan_bytes_and_parser_details_refuse[raw2]",
    "test_plan_bytes_and_parser_details_refuse[]",
    "test_plan_bytes_and_parser_details_refuse[{]",
    "test_plan_bytes_and_parser_details_refuse[null]",
    "test_plan_bytes_and_parser_details_refuse[[]]",
    'test_plan_bytes_and_parser_details_refuse[{"schema":"wd.q4-prefix-build-plan.v1","schema":"x"}]',
    'test_plan_bytes_and_parser_details_refuse[{"schema":"wd.q4-prefix-build-plan.v1","timeout":NaN}]',
    "test_plan_over_the_byte_limit_refuses",
    "test_inference_uses_offline_environment_then_restores_real_environment[False]",
    "test_inference_uses_offline_environment_then_restores_real_environment[True]",
)
EXPECTED_NODEIDS = tuple(TEST_PATH + "::" + name for name in _NAMES)


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
    code = int(pytest.main([*(str(ROOT / TEST_PATH) + "::" + name for name in _GROUPS), "-q"], plugins=[guard]))
    return 1 if code == 0 and not guard.complete() else code


def main():
    sys.path.insert(0, str(ROOT))
    import pytest
    return _run_pytest(pytest)


if __name__ == "__main__":
    raise SystemExit(main())
