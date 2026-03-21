#!/usr/bin/env python3
"""
WaggleDance — Phase 10: TrainingDataCollector Tests
=====================================================
15 tests across 4 groups:
  1. Syntax (1): training_collector.py parses OK
  2. Init & Config (3): init defaults, constants, stats empty
  3. collect_training_pair (6): accept valid, reject low confidence,
     reject short answer, reject empty, reject duplicate, counter updates
  4. Export & Stats (5): export_for_v1, export_for_v2, get_training_data,
     save_pairs, collect_all no consciousness
"""

import ast
import json
import os
import sys
import tempfile
import unittest

_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class TestSyntax(unittest.TestCase):
    def test_training_collector_syntax(self):
        fpath = os.path.join(_project_root, "core", "training_collector.py")
        self.assertTrue(os.path.exists(fpath), "Missing: core/training_collector.py")
        with open(fpath, encoding="utf-8") as f:
            source = f.read()
        ast.parse(source, filename="core/training_collector.py")


class TestInitAndConfig(unittest.TestCase):
    def setUp(self):
        from core.training_collector import TrainingDataCollector
        self.TrainingDataCollector = TrainingDataCollector
        self.collector = TrainingDataCollector(consciousness=None)

    def test_init_defaults(self):
        c = self.collector
        self.assertEqual(c._total_collected, 0)
        self.assertEqual(c._total_rejected, 0)
        self.assertEqual(len(c._pairs), 0)
        self.assertEqual(len(c._seen_keys), 0)

    def test_confidence_thresholds_present(self):
        thresholds = self.TrainingDataCollector.CONFIDENCE_THRESHOLDS
        self.assertIn("round_table_consensus", thresholds)
        self.assertIn("user_accepted", thresholds)
        self.assertIn("expert_distillation", thresholds)
        self.assertGreaterEqual(thresholds["round_table_consensus"], 0.85)
        self.assertGreaterEqual(thresholds["expert_distillation"], 0.90)

    def test_stats_empty(self):
        stats = self.collector.stats
        self.assertEqual(stats["total_pairs"], 0)
        self.assertEqual(stats["total_collected"], 0)
        self.assertEqual(stats["total_rejected"], 0)
        self.assertIsInstance(stats["sources"], dict)


class TestCollectTrainingPair(unittest.TestCase):
    def setUp(self):
        from core.training_collector import TrainingDataCollector
        self.collector = TrainingDataCollector(consciousness=None)

    def test_accept_valid_pair(self):
        ok = self.collector.collect_training_pair(
            "Mikä on varroa?",
            "Varroa destructor on loinen, joka vahingoittaa mehiläisiä.",
            "test",
            0.90
        )
        self.assertTrue(ok)
        self.assertEqual(self.collector._total_collected, 1)

    def test_reject_low_confidence(self):
        ok = self.collector.collect_training_pair(
            "Kysymys?",
            "Tässä on pitkä vastaus joka täyttää minimipituuden helposti.",
            "test",
            0.50  # below MIN_TRAINING_CONFIDENCE=0.75
        )
        self.assertFalse(ok)
        self.assertEqual(self.collector._total_rejected, 1)

    def test_reject_short_answer(self):
        ok = self.collector.collect_training_pair(
            "Lyhyt?",
            "Lyhyt",  # < 10 chars
            "test",
            0.90
        )
        self.assertFalse(ok)

    def test_reject_empty_question(self):
        ok = self.collector.collect_training_pair(
            "",
            "Vastaus joka on tarpeeksi pitkä tähän testiin.",
            "test",
            0.90
        )
        self.assertFalse(ok)

    def test_reject_duplicate(self):
        q = "Mikä on mehiläinen?"
        a = "Mehiläinen on hyönteinen joka kerää siitepölyä kukista."
        self.collector.collect_training_pair(q, a, "test", 0.90)
        ok2 = self.collector.collect_training_pair(q, a, "test", 0.90)
        self.assertFalse(ok2)
        self.assertEqual(len(self.collector._pairs), 1)

    def test_counter_updates(self):
        self.collector.collect_training_pair(
            "Kysymys yksi?",
            "Vastaus yksi joka on tarpeeksi pitkä.",
            "src",
            0.85
        )
        self.collector.collect_training_pair(
            "",  # will be rejected
            "Vastaus",
            "src",
            0.90
        )
        self.assertEqual(self.collector._total_collected, 1)
        self.assertEqual(self.collector._total_rejected, 1)


class TestExportAndStats(unittest.TestCase):
    def setUp(self):
        from core.training_collector import TrainingDataCollector
        self.collector = TrainingDataCollector(consciousness=None)
        # Add 10 pairs to satisfy export_for_v2 minimum
        base_pairs = [
            ("Mikä on varroa?", "Varroa destructor on mehiläisten loinen.", "round_table_consensus", 0.92),
            ("Mitä tarkoittaa parveilu?", "Parveilu on prosessi jossa mehiläisyhdyskunta jakautuu.", "user_accepted", 0.88),
            ("Mitkä ovat kevään tarkastukset?", "Keväällä tarkastetaan pesät, kuningatar ja poikasalue.", "chromadb_high_confidence", 0.80),
            ("Miten varroa hoidetaan?", "Varroaa hoidetaan oksaalihapolla tai muurahaishapoilla syksyllä.", "user_accepted", 0.90),
            ("Milloin linkous tapahtuu?", "Linkous tapahtuu kun hunajakennot ovat peitetty noin 75-prosenttisesti.", "chromadb_high_confidence", 0.80),
            ("Mikä on emomehiläinen?", "Emomehiläinen on yhdyskunnan ainoa lisääntyvä naaras ja munii jopa 2000 munaa päivässä.", "round_table_consensus", 0.91),
            ("Mitä on propolis?", "Propolis on mehiläisten keräämä hartsinen aine jota käytetään pesän tiivistykseen.", "user_accepted", 0.85),
            ("Kuinka monta mehiläistä pesässä on?", "Kesäisin vahvassa yhdyskunnassa voi olla 50000-80000 mehiläistä.", "chromadb_high_confidence", 0.80),
            ("Mikä on siitepölyn rooli?", "Siitepöly on mehiläisten proteiinin lähde ja välttämätön toukkien kasvulle.", "user_accepted", 0.87),
            ("Mitä tarkoittaa talvehtiminen?", "Talvehtiminen tarkoittaa mehiläisyhdyskunnan selviytymistä talvikauden yli.", "chromadb_high_confidence", 0.80),
        ]
        for q, a, s, c in base_pairs:
            self.collector.collect_training_pair(q, a, s, c)

    def test_export_for_v1(self):
        pairs = self.collector.export_for_v1()
        self.assertIsInstance(pairs, list)
        # Only confidence >= 0.90 pairs
        for p in pairs:
            self.assertIn("pattern", p)
            self.assertIn("answer", p)
            self.assertIn("confidence", p)
            self.assertGreaterEqual(p["confidence"], 0.90)

    def test_export_for_v2(self):
        result = self.collector.export_for_v2()
        self.assertIsNotNone(result)
        self.assertIn("questions", result)
        self.assertIn("answer_ids", result)
        self.assertIn("answers", result)
        self.assertEqual(len(result["questions"]), len(result["answer_ids"]))

    def test_get_training_data_sufficient(self):
        data = self.collector.get_training_data(min_pairs=10)
        self.assertIsNotNone(data)
        self.assertEqual(len(data), 10)

    def test_get_training_data_insufficient(self):
        data = self.collector.get_training_data(min_pairs=100)
        self.assertIsNone(data)

    def test_collect_all_no_consciousness(self):
        """collect_all should not raise without consciousness."""
        c = self.collector.__class__(consciousness=None)
        total = c.collect_all()
        self.assertGreaterEqual(total, 0)

    def test_save_and_reset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from core.training_collector import TrainingDataCollector
            c = TrainingDataCollector(consciousness=None, data_dir=tmpdir)
            c.collect_training_pair(
                "Talvetus?",
                "Talvetus tarkoittaa mehiläisten talvihoitoa.",
                "test",
                0.85
            )
            c.save_pairs()
            out = os.path.join(tmpdir, "training_pairs.jsonl")
            self.assertTrue(os.path.exists(out))
            with open(out, encoding="utf-8") as f:
                lines = [l for l in f if l.strip()]
            self.assertEqual(len(lines), 1)
            pair = json.loads(lines[0])
            self.assertIn("question", pair)
            self.assertIn("answer", pair)
            c.reset()
            self.assertEqual(c._total_collected, 0)
            self.assertEqual(len(c._pairs), 0)


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Phase 10: TrainingDataCollector — Test Suite")
    print("=" * 60 + "\n")
    unittest.main(verbosity=2)
