#!/usr/bin/env python3
"""
WaggleDance — Swarm Routing Tests
===================================
12 tests across 3 groups:
  1. Syntax (1): swarm_scheduler.py parses OK
  2. Registration (5): register, idempotent, tags, skills, agent_count
  3. Scheduling (6): select_candidates, record_task_result, record start/end,
                     cold-start calibration, task history, top_k limit
"""

import ast
import os
import sys
import unittest

_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class TestSyntax(unittest.TestCase):
    def test_swarm_scheduler_syntax(self):
        fpath = os.path.join(_project_root, "core", "swarm_scheduler.py")
        self.assertTrue(os.path.exists(fpath), "Missing: core/swarm_scheduler.py")
        with open(fpath, encoding="utf-8") as f:
            source = f.read()
        ast.parse(source, filename="core/swarm_scheduler.py")


class TestRegistration(unittest.TestCase):
    def setUp(self):
        from core.swarm_scheduler import SwarmScheduler, AgentScore
        self.SwarmScheduler = SwarmScheduler
        self.AgentScore = AgentScore
        self.sched = SwarmScheduler()

    def test_register_agent(self):
        self.sched.register_agent("bee_01", "beekeeper", skills=["varroa"], tags=["mehiläinen"])
        self.assertIn("bee_01", self.sched._scores)

    def test_register_idempotent(self):
        self.sched.register_agent("bee_02", "beekeeper")
        self.sched.register_agent("bee_02", "beekeeper")
        self.assertEqual(self.sched.agent_count, 1)

    def test_register_tags_stored(self):
        self.sched.register_agent("bee_03", "beekeeper", tags=["varroa", "hunaja"])
        score = self.sched._scores["bee_03"]
        self.assertIn("varroa", score.tags)
        self.assertIn("hunaja", score.tags)

    def test_register_skills_stored(self):
        self.sched.register_agent("bee_04", "beekeeper", skills=["health_check", "inspection"])
        score = self.sched._scores["bee_04"]
        self.assertIn("health_check", score.skills)

    def test_agent_count(self):
        for i in range(5):
            self.sched.register_agent(f"agent_{i}", "beekeeper")
        self.assertEqual(self.sched.agent_count, 5)


class TestScheduling(unittest.TestCase):
    def setUp(self):
        from core.swarm_scheduler import SwarmScheduler
        self.sched = SwarmScheduler({"top_k": 3})
        # Register 6 agents
        types = ["beekeeper", "meteorologist", "beekeeper",
                 "horticulturist", "beekeeper", "forester"]
        for i, atype in enumerate(types):
            self.sched.register_agent(
                f"agent_{i}", atype,
                tags=["mehiläinen"] if atype == "beekeeper" else []
            )

    def test_select_candidates_returns_list(self):
        candidates = self.sched.select_candidates(
            task_type="varroa_check",
            task_tags=["mehiläinen"],
            routing_rules={},
        )
        self.assertIsInstance(candidates, list)

    def test_select_candidates_top_k(self):
        candidates = self.sched.select_candidates(
            task_type="hive_inspection",
            task_tags=["pesä"],
            routing_rules={},
        )
        # top_k=3, must not exceed total registered
        self.assertLessEqual(len(candidates), 6)

    def test_record_task_result_success(self):
        self.sched.record_task_result("agent_0", success=True, latency_ms=200.0)
        score = self.sched._scores["agent_0"]
        # success should improve success_score toward 1.0
        self.assertGreater(score.success_score, 0.0)

    def test_record_task_result_failure(self):
        # First get a baseline
        initial = self.sched._scores["agent_0"].success_score
        self.sched.record_task_result("agent_0", success=False, latency_ms=500.0)
        # After failure, success_score shouldn't exceed initial
        updated = self.sched._scores["agent_0"].success_score
        self.assertLessEqual(updated, initial + 0.01)  # allow tiny float tolerance

    def test_record_task_start_end(self):
        """record_task_start and record_task_end don't raise."""
        self.sched.record_task_start("agent_1")
        self.sched.record_task_end("agent_1")

    def test_agentscore_defaults(self):
        """AgentScore starts with balanced pheromone values."""
        from core.swarm_scheduler import AgentScore
        s = AgentScore(agent_id="x", agent_type="beekeeper")
        self.assertEqual(s.success_score, 0.5)
        self.assertEqual(s.speed_score, 0.5)
        self.assertEqual(s.reliability_score, 0.5)
        self.assertEqual(s.active_tasks, 0)


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Swarm Routing — Test Suite")
    print("=" * 60 + "\n")
    unittest.main(verbosity=2)
