"""F5 synthetic observation contracts; no real process/token/ACL/mutex access."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from tools.bridge_lock_participants import ParticipantInputError, participant_snapshot

NOW = datetime(2026, 9, 30, 19, 10, tzinfo=timezone.utc)
STAMP = "2026-09-30T19:09:30Z"


def fixture():
    values = {"pid": 123, "start": "2026-09-30T18:00:00Z",
              "token": {"user_sid": "S-1-5-21-123", "elevated": False},
              "acl": {"owner_sid": "S-1-5-21-123", "readable": True, "writable": False},
              "root": "C:/synthetic/bridge",
              "mutex": {"name": "Local\\synthetic", "observation_kind": "participant_report"}}
    return {"schema": "wd.bridge-participant-observations.v1", "participants": {
        "tools-fixture": {name: {"value": value, "observed_at_utc": STAMP,
                                  "max_age_seconds": 60, "source": "fixture"}
                          for name, value in values.items()}}}


class ParticipantTests(unittest.TestCase):
    def test_success_twin_is_observed_not_complete_or_ready(self):
        result = participant_snapshot(fixture(), NOW)
        facts = result["participants"]["tools-fixture"]
        self.assertTrue(all(row["state"] == "observed" for row in facts.values()))
        self.assertFalse(result["all_mutex_holders_verified"])
        self.assertNotIn("ready", result)
        self.assertEqual(result["authority_effect"], "none")

    def test_pid_never_synthesizes_other_facts(self):
        value = fixture()
        value["participants"]["tools-fixture"] = {"pid": value["participants"]["tools-fixture"]["pid"]}
        facts = participant_snapshot(value, NOW)["participants"]["tools-fixture"]
        self.assertEqual(facts["pid"]["state"], "observed")
        self.assertTrue(all(row["state"] == "unknown" for key, row in facts.items() if key != "pid"))

    def test_each_fact_ages_without_borrowing_pid_timestamp(self):
        value = fixture()
        value["participants"]["tools-fixture"]["token"]["observed_at_utc"] = "2026-09-30T18:00:00Z"
        facts = participant_snapshot(value, NOW)["participants"]["tools-fixture"]
        self.assertEqual(facts["token"]["reason"], "stale")
        self.assertIsNone(facts["token"]["value"])
        self.assertEqual(facts["pid"]["state"], "observed")

    def test_unreadable_and_conflicting_remain_unknown(self):
        value = fixture()
        value["participants"]["tools-fixture"]["acl"]["read_error"] = "secret diagnostic"
        value["participants"]["tools-fixture"]["mutex"]["conflicting"] = True
        result = participant_snapshot(value, NOW)
        facts = result["participants"]["tools-fixture"]
        self.assertEqual(facts["acl"]["reason"], "unreadable")
        self.assertEqual(facts["mutex"]["reason"], "conflicting")
        self.assertNotIn("secret diagnostic", str(result))

    def test_missing_source_or_start_time_never_verified(self):
        value = fixture()
        del value["participants"]["tools-fixture"]["token"]["source"]
        value["participants"]["tools-fixture"]["start"]["value"] = "not a timestamp"
        facts = participant_snapshot(value, NOW)["participants"]["tools-fixture"]
        self.assertEqual(facts["token"]["reason"], "invalid_source")
        self.assertEqual(facts["start"]["reason"], "invalid_value")

    def test_age_boundaries_and_future_unknown(self):
        for stamp, limit, expected in ((STAMP, 30, "observed"), (STAMP, 29, "unknown"),
                                       ("2026-09-30T19:10:01Z", 60, "unknown")):
            value = fixture()
            value["participants"]["tools-fixture"]["pid"].update(observed_at_utc=stamp, max_age_seconds=limit)
            self.assertEqual(participant_snapshot(value, NOW)["participants"]["tools-fixture"]["pid"]["state"], expected)

    def test_invalid_age_and_pid_types_unknown(self):
        for age in (True, 0, -1, "60", 1.5):
            value = fixture()
            value["participants"]["tools-fixture"]["pid"]["max_age_seconds"] = age
            self.assertEqual(participant_snapshot(value, NOW)["participants"]["tools-fixture"]["pid"]["state"], "unknown")
        for pid in (True, 0, -1, "123"):
            value = fixture()
            value["participants"]["tools-fixture"]["pid"]["value"] = pid
            self.assertEqual(participant_snapshot(value, NOW)["participants"]["tools-fixture"]["pid"]["reason"], "invalid_value")

    def test_token_credentials_or_acl_guesses_not_displayed(self):
        for name, data in (("token", {"access_token": "SECRET"}), ("acl", {"writable": "yes"}),
                           ("mutex", {"name": "m", "observation_kind": "all_holders"})):
            value = fixture()
            value["participants"]["tools-fixture"][name]["value"] = data
            result = participant_snapshot(value, NOW)
            self.assertEqual(result["participants"]["tools-fixture"][name]["state"], "unknown")
            self.assertNotIn("SECRET", str(result))

    def test_no_mutation_no_io_deterministic_and_no_output_alias(self):
        value = fixture()
        before = deepcopy(value)
        with patch("builtins.open", side_effect=AssertionError("no reads")):
            first = participant_snapshot(value, NOW)
            self.assertEqual(first, participant_snapshot(value, NOW))
        first["participants"]["tools-fixture"]["token"]["value"]["elevated"] = True
        self.assertEqual(value, before)

    def test_invalid_envelope_and_unknown_facts_refused(self):
        for value in ({}, [], {"schema": "other"},
                      {"schema": "wd.bridge-participant-observations.v1", "participants": []}):
            with self.assertRaises(ParticipantInputError):
                participant_snapshot(value, NOW)
        value = fixture()
        value["participants"]["tools-fixture"]["ready"] = True
        with self.assertRaises(ParticipantInputError):
            participant_snapshot(value, NOW)
        with self.assertRaises(ParticipantInputError):
            participant_snapshot(fixture(), NOW.replace(tzinfo=None))

    def test_conflict_marker_must_be_exact_boolean(self):
        value = fixture()
        value["participants"]["tools-fixture"]["pid"]["conflicting"] = "false"
        self.assertEqual(participant_snapshot(value, NOW)["participants"]["tools-fixture"]["pid"]["state"], "unknown")

    def test_coverage_retains_fact_source_and_observation_age(self):
        result = participant_snapshot(fixture(), NOW)
        fact = result["participants"]["tools-fixture"]["acl"]
        self.assertEqual(fact["source"], "fixture")
        self.assertEqual(fact["age_seconds"], 30)
        self.assertEqual(result["evaluated_at_utc"], NOW.isoformat())

    def test_future_process_start_is_unknown(self):
        value = fixture()
        value["participants"]["tools-fixture"]["start"]["value"] = "2026-09-30T19:11:00Z"
        self.assertEqual(participant_snapshot(value, NOW)["participants"]["tools-fixture"]["start"]["reason"], "future_process_start")

    def test_absent_token_and_acl_subfields_are_not_defaulted(self):
        value = fixture()
        value["participants"]["tools-fixture"]["token"]["value"] = {"user_sid": "S-1-5-21-123"}
        value["participants"]["tools-fixture"]["acl"]["value"] = {"owner_sid": "S-1-5-21-123"}
        facts = participant_snapshot(value, NOW)["participants"]["tools-fixture"]
        self.assertNotIn("elevated", facts["token"]["value"])
        self.assertNotIn("writable", facts["acl"]["value"])

    def test_empty_snapshot_proves_no_completeness(self):
        result = participant_snapshot({"schema": "wd.bridge-participant-observations.v1", "participants": {}}, NOW)
        self.assertEqual(result["participants"], {})
        self.assertFalse(result["all_mutex_holders_verified"])

    def test_nonfinite_and_unknown_top_level_data_refused(self):
        value = fixture()
        value["participants"]["tools-fixture"]["pid"]["value"] = float("nan")
        with self.assertRaises(ParticipantInputError):
            participant_snapshot(value, NOW)
        value = fixture()
        value["complete"] = True
        with self.assertRaises(ParticipantInputError):
            participant_snapshot(value, NOW)


if __name__ == "__main__":
    unittest.main()
