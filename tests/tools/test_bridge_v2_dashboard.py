"""Pure snapshot contracts; no live runtime, provider or collector access."""
import copy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from tools.bridge_v2_dashboard import DashboardInputError, dashboard


NOW = datetime(2026, 9, 30, 17, 0, tzinfo=timezone.utc)
STAMP = "2026-09-30T16:59:30Z"


def fixture():
    return {
        "schema": "wd.bridge-dashboard-snapshot.v1",
        "sources": {
            "package": {"observed_at_utc": STAMP, "max_age_seconds": 60,
                        "data": {"commit": "7779", "pins_verified": True}},
            "claims": {"observed_at_utc": STAMP, "max_age_seconds": 60,
                       "data": {"count": 2}},
        },
        "lanes": {"codex-tools-1": {
            dimension: {"state": state, "observed_at_utc": STAMP,
                        "max_age_seconds": 60}
            for dimension, state in {"cli": "installed", "auth": "valid",
                                     "quota": "available", "observed_turn": "succeeded"}.items()
        }},
    }


class DashboardTests(unittest.TestCase):
    def test_fresh_success_twin_has_independent_states_not_readiness(self):
        result = dashboard(fixture(), NOW)
        self.assertEqual(result["sources"]["package"]["state"], "fresh")
        self.assertEqual(result["lanes"]["codex-tools-1"]["auth"]["state"], "valid")
        self.assertNotIn("ready", result["lanes"]["codex-tools-1"])
        self.assertEqual(result["authority_effect"], "none")

    def test_each_source_ages_independently(self):
        value = fixture()
        value["sources"]["package"]["observed_at_utc"] = "2026-09-30T16:00:00Z"
        result = dashboard(value, NOW)
        self.assertEqual(result["sources"]["package"]["reason"], "stale")
        self.assertIsNone(result["sources"]["package"]["data"])
        self.assertEqual(result["sources"]["claims"]["state"], "fresh")

    def test_capacity_does_not_inherit_cli_or_callback_success(self):
        value = fixture()
        lane = value["lanes"]["codex-tools-1"]
        lane["quota"]["observed_at_utc"] = "2026-09-30T16:00:00Z"
        del lane["auth"]
        lane["observed_turn"]["state"] = "callback"
        result = dashboard(value, NOW)["lanes"]["codex-tools-1"]
        self.assertEqual(result["cli"]["state"], "installed")
        for dimension in ("quota", "auth", "observed_turn"):
            self.assertEqual(result[dimension]["state"], "unknown")

    def test_required_unreadable_source_is_visible(self):
        value = fixture()
        value["sources"]["package"] = {"required": True, "read_error": "denied"}
        result = dashboard(value, NOW)
        self.assertEqual(result["sources"]["package"]["reason"], "unreadable")
        self.assertIn({"source": "package", "reason": "unreadable"}, result["findings"])

    def test_unknown_missing_and_future_are_not_fresh(self):
        value = fixture()
        value["sources"]["package"]["observed_at_utc"] = "2026-09-30T17:00:01Z"
        result = dashboard(value, NOW)
        self.assertEqual(result["sources"]["package"]["reason"], "future_dated")
        self.assertEqual(result["sources"]["stage"]["reason"], "absent")

    def test_exact_age_boundary(self):
        value = fixture()
        value["sources"]["package"]["max_age_seconds"] = 30
        self.assertEqual(dashboard(value, NOW)["sources"]["package"]["state"], "fresh")
        value["sources"]["package"]["max_age_seconds"] = 29
        self.assertEqual(dashboard(value, NOW)["sources"]["package"]["reason"], "stale")

    def test_no_input_mutation_or_io_and_no_output_alias(self):
        value = fixture()
        original = copy.deepcopy(value)
        with patch("builtins.open", side_effect=AssertionError("I/O forbidden")):
            result = dashboard(value, NOW)
        self.assertEqual(value, original)
        result["sources"]["package"]["data"]["commit"] = "changed"
        self.assertEqual(value, original)
        self.assertEqual(dashboard(value, NOW), dashboard(value, NOW))

    def test_invalid_envelope_refused(self):
        for value in ({}, {"schema": "other"}, [],
                      {"schema": "wd.bridge-dashboard-snapshot.v1", "sources": []}):
            with self.assertRaises(DashboardInputError):
                dashboard(value, NOW)
        with self.assertRaises(DashboardInputError):
            dashboard(fixture(), NOW.replace(tzinfo=None))

    def test_bad_age_types_and_bad_state_stay_unknown(self):
        for age in (True, 0, -1, "60", 1.5):
            value = fixture()
            value["lanes"]["codex-tools-1"]["quota"]["max_age_seconds"] = age
            self.assertEqual(dashboard(value, NOW)["lanes"]["codex-tools-1"]["quota"]["state"], "unknown")

    def test_negative_states_remain_distinct(self):
        value = fixture()
        expected = {"cli": "missing", "auth": "invalid", "quota": "exhausted",
                    "observed_turn": "failed"}
        for dimension, state in expected.items():
            value["lanes"]["codex-tools-1"][dimension]["state"] = state
        result = dashboard(value, NOW)["lanes"]["codex-tools-1"]
        self.assertEqual({key: row["state"] for key, row in result.items()}, expected)

    def test_required_missing_data_is_a_finding(self):
        value = fixture()
        del value["sources"]["package"]["data"]
        value["sources"]["package"]["required"] = True
        self.assertIn({"source": "package", "reason": "missing_data"}, dashboard(value, NOW)["findings"])

    def test_non_json_unknown_fields_and_malformed_lanes_refused(self):
        mutations = (
            lambda v: v.update(extra=True),
            lambda v: v["sources"].update(extra={}),
            lambda v: v["sources"]["package"].update(required="yes"),
            lambda v: v["sources"]["package"].update(data=float("nan")),
            lambda v: v["lanes"].update({1: {}}),
            lambda v: v["lanes"]["codex-tools-1"].update(ready=True),
        )
        for mutate in mutations:
            value = fixture()
            mutate(value)
            with self.assertRaises(DashboardInputError):
                dashboard(value, NOW)


    def test_lane_charset_and_length_refusal_twins(self):
        for lane in ('Uppercase', 'bad lane', 'x' * 65):
            value = fixture()
            value['lanes'] = {lane: value['lanes']['codex-tools-1']}
            with self.subTest(lane=lane), self.assertRaises(DashboardInputError):
                dashboard(value, NOW)
        value = fixture()
        value['lanes'] = {'x' * 64: value['lanes']['codex-tools-1']}
        self.assertEqual(dashboard(value, NOW)['lanes']['x' * 64]['auth']['state'], 'valid')

    def test_source_and_dimension_max_age_upper_bound_refusal_twins(self):
        for source in (True, False):
            value = fixture()
            entry = value['sources']['package'] if source else value['lanes']['codex-tools-1']['auth']
            entry['max_age_seconds'] = 604801
            result = dashboard(value, NOW)
            fact = result['sources']['package'] if source else result['lanes']['codex-tools-1']['auth']
            self.assertEqual((fact['state'], fact['reason']), ('unknown', 'invalid_max_age'))
            if source:
                self.assertIsNone(fact['data'])
            entry['max_age_seconds'] = 604800
            result = dashboard(value, NOW)
            fact = result['sources']['package'] if source else result['lanes']['codex-tools-1']['auth']
            self.assertEqual(fact['state'], 'fresh' if source else 'valid')


if __name__ == "__main__":
    unittest.main()
