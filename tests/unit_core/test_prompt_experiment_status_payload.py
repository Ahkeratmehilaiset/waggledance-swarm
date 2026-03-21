"""Tests for core.prompt_experiment_status — ExperimentSummary and ExperimentStatusFormatter."""

import unittest

from core.prompt_experiment_status import ExperimentSummary, ExperimentStatusFormatter


class TestExperimentSummary(unittest.TestCase):
    def test_default_values(self):
        s = ExperimentSummary(id="exp-1", status="running")
        self.assertEqual(s.id, "exp-1")
        self.assertEqual(s.status, "running")
        self.assertEqual(s.baseline_score, 0.0)
        self.assertEqual(s.current_score, 0.0)
        self.assertEqual(s.improvement, 0.0)
        self.assertEqual(s.samples, 0)
        self.assertEqual(s.created_at, 0.0)

    def test_to_dict_all_fields(self):
        s = ExperimentSummary(
            id="exp-42", status="completed",
            baseline_score=0.7234, current_score=0.8567,
            improvement=0.1333, samples=500, created_at=1700000000.0,
        )
        d = s.to_dict()
        self.assertEqual(d["id"], "exp-42")
        self.assertEqual(d["status"], "completed")
        self.assertEqual(d["baseline_score"], 0.723)
        self.assertEqual(d["current_score"], 0.857)
        self.assertEqual(d["improvement"], 0.133)
        self.assertEqual(d["samples"], 500)
        self.assertEqual(d["created_at"], 1700000000.0)

    def test_to_dict_rounds_to_3_decimals(self):
        s = ExperimentSummary(
            id="r", status="running",
            baseline_score=0.12345, current_score=0.67891,
            improvement=0.55555,
        )
        d = s.to_dict()
        self.assertEqual(d["baseline_score"], 0.123)
        self.assertEqual(d["current_score"], 0.679)
        self.assertEqual(d["improvement"], 0.556)


class TestExperimentStatusFormatter(unittest.TestCase):
    def test_format_experiment_full_data(self):
        raw = {
            "id": "exp-1", "status": "running",
            "baseline_score": 0.5, "current_score": 0.6,
            "improvement": 0.1, "samples": 100, "created_at": 1000.0,
        }
        summary = ExperimentStatusFormatter.format_experiment(raw)
        self.assertIsInstance(summary, ExperimentSummary)
        self.assertEqual(summary.id, "exp-1")
        self.assertEqual(summary.status, "running")
        self.assertEqual(summary.samples, 100)

    def test_format_experiment_missing_fields_defaults(self):
        raw = {}
        summary = ExperimentStatusFormatter.format_experiment(raw)
        self.assertEqual(summary.id, "")
        self.assertEqual(summary.status, "unknown")
        self.assertEqual(summary.baseline_score, 0.0)
        self.assertEqual(summary.samples, 0)

    def test_format_experiment_partial_data(self):
        raw = {"id": "partial", "status": "rolled_back", "samples": 50}
        summary = ExperimentStatusFormatter.format_experiment(raw)
        self.assertEqual(summary.id, "partial")
        self.assertEqual(summary.status, "rolled_back")
        self.assertEqual(summary.samples, 50)
        self.assertEqual(summary.current_score, 0.0)

    def test_format_list_empty(self):
        result = ExperimentStatusFormatter.format_list([])
        self.assertEqual(result, [])

    def test_format_list_multiple(self):
        raw_list = [
            {"id": "a", "status": "running", "samples": 10},
            {"id": "b", "status": "completed", "samples": 200},
            {"id": "c", "status": "rolled_back", "improvement": -0.05},
        ]
        result = ExperimentStatusFormatter.format_list(raw_list)
        self.assertEqual(len(result), 3)
        # Each entry should be a dict (from to_dict())
        self.assertIsInstance(result[0], dict)
        self.assertEqual(result[0]["id"], "a")
        self.assertEqual(result[1]["status"], "completed")
        self.assertEqual(result[2]["improvement"], -0.05)

    def test_format_list_preserves_order(self):
        raw_list = [
            {"id": f"exp-{i}", "status": "running"} for i in range(5)
        ]
        result = ExperimentStatusFormatter.format_list(raw_list)
        ids = [r["id"] for r in result]
        self.assertEqual(ids, ["exp-0", "exp-1", "exp-2", "exp-3", "exp-4"])


if __name__ == "__main__":
    unittest.main()
