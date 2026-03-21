"""Tests for tools/migrate_db.py — schema migration + auth + training tools."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestMigrateDBSyntax(unittest.TestCase):
    def test_01_imports(self):
        import tools.migrate_db as m
        self.assertTrue(hasattr(m, 'check_all'))
        self.assertTrue(hasattr(m, 'migrate_all'))
        self.assertTrue(hasattr(m, 'LATEST_VERSIONS'))


class TestSchemaVersion(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _create_db(self, name, tables_sql=""):
        path = self.data_dir / name
        conn = sqlite3.connect(str(path))
        if tables_sql:
            conn.executescript(tables_sql)
        conn.close()
        return path

    def test_10_check_missing_db(self):
        from tools.migrate_db import check_db
        r = check_db("audit_log.db", self.data_dir)
        self.assertFalse(r["exists"])
        self.assertEqual(r["status"], "MISSING")

    def test_11_check_fresh_db(self):
        from tools.migrate_db import check_db
        self._create_db("audit_log.db", """
            CREATE TABLE audit (
                id INTEGER PRIMARY KEY, timestamp REAL, action TEXT,
                doc_id TEXT, collection TEXT DEFAULT '', layer TEXT DEFAULT '',
                agent_id TEXT DEFAULT '', session_id TEXT DEFAULT '',
                spawn_chain TEXT DEFAULT '', content_hash TEXT DEFAULT '',
                metadata TEXT DEFAULT '{}', details TEXT DEFAULT ''
            );
        """)
        r = check_db("audit_log.db", self.data_dir)
        self.assertTrue(r["exists"])
        self.assertEqual(r["version"], 1)  # No schema_version = v1
        self.assertEqual(r["status"], "NEEDS_MIGRATION")

    def test_12_migrate_audit_log(self):
        from tools.migrate_db import migrate_db, check_db
        self._create_db("audit_log.db", """
            CREATE TABLE audit (
                id INTEGER PRIMARY KEY, timestamp REAL, action TEXT,
                doc_id TEXT, collection TEXT DEFAULT '', layer TEXT DEFAULT '',
                agent_id TEXT DEFAULT '', session_id TEXT DEFAULT '',
                spawn_chain TEXT DEFAULT '', content_hash TEXT DEFAULT '',
                metadata TEXT DEFAULT '{}', details TEXT DEFAULT ''
            );
        """)
        changed = migrate_db("audit_log.db", self.data_dir)
        self.assertTrue(changed)

        r = check_db("audit_log.db", self.data_dir)
        self.assertEqual(r["version"], 2)
        self.assertEqual(r["status"], "OK")

        # Verify column exists
        conn = sqlite3.connect(str(self.data_dir / "audit_log.db"))
        cols = [c[1] for c in conn.execute("PRAGMA table_info(audit)").fetchall()]
        self.assertIn("content_preview", cols)
        conn.close()

    def test_13_migrate_waggle_dance(self):
        from tools.migrate_db import migrate_db, check_db
        self._create_db("waggle_dance.db", """
            CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT,
                agent_id TEXT, memory_type TEXT DEFAULT 'observation',
                importance REAL DEFAULT 0.5, created_at TIMESTAMP);
            CREATE TABLE events (id INTEGER PRIMARY KEY, agent_id TEXT,
                event_type TEXT, description TEXT, created_at TIMESTAMP);
            CREATE TABLE messages (id INTEGER PRIMARY KEY, from_agent TEXT,
                to_agent TEXT, content TEXT, read INTEGER DEFAULT 0,
                created_at TIMESTAMP);
            CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT,
                status TEXT DEFAULT 'pending', created_at TIMESTAMP);
        """)
        changed = migrate_db("waggle_dance.db", self.data_dir)
        self.assertTrue(changed)

        r = check_db("waggle_dance.db", self.data_dir)
        self.assertEqual(r["version"], 2)
        self.assertEqual(r["status"], "OK")

    def test_14_idempotent_migration(self):
        from tools.migrate_db import migrate_db
        self._create_db("audit_log.db", """
            CREATE TABLE audit (
                id INTEGER PRIMARY KEY, timestamp REAL, action TEXT,
                doc_id TEXT, collection TEXT DEFAULT '', layer TEXT DEFAULT '',
                agent_id TEXT DEFAULT '', session_id TEXT DEFAULT '',
                spawn_chain TEXT DEFAULT '', content_hash TEXT DEFAULT '',
                metadata TEXT DEFAULT '{}', details TEXT DEFAULT ''
            );
        """)
        migrate_db("audit_log.db", self.data_dir)
        changed = migrate_db("audit_log.db", self.data_dir)
        self.assertFalse(changed)  # Already at latest

    def test_15_check_all(self):
        from tools.migrate_db import check_all
        self._create_db("audit_log.db")
        self._create_db("waggle_dance.db")
        results = check_all(self.data_dir)
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertIn("db", r)
            self.assertIn("status", r)

    def test_16_migrate_all(self):
        from tools.migrate_db import migrate_all, check_all
        self._create_db("audit_log.db", """
            CREATE TABLE audit (id INTEGER PRIMARY KEY, timestamp REAL,
                action TEXT, doc_id TEXT);
        """)
        self._create_db("waggle_dance.db", """
            CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT,
                agent_id TEXT, memory_type TEXT);
            CREATE TABLE events (id INTEGER PRIMARY KEY, agent_id TEXT,
                event_type TEXT);
            CREATE TABLE messages (id INTEGER PRIMARY KEY, to_agent TEXT,
                read INTEGER);
            CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT);
        """)
        changed = migrate_all(self.data_dir)
        self.assertEqual(changed, 2)

        for r in check_all(self.data_dir):
            self.assertEqual(r["status"], "OK")

    def test_17_schema_version_table_structure(self):
        from tools.migrate_db import _ensure_schema_version_table, _get_version, _set_version
        path = self._create_db("test.db")
        conn = sqlite3.connect(str(path))
        _ensure_schema_version_table(conn)
        _set_version(conn, 5)
        self.assertEqual(_get_version(conn), 5)
        conn.close()


class TestBearerAuth(unittest.TestCase):
    """Tests for backend/auth.py."""

    def test_20_import(self):
        from backend.auth import BearerAuthMiddleware, get_or_create_api_key, PUBLIC_PATHS
        self.assertIn("/health", PUBLIC_PATHS)
        self.assertIn("/api/status", PUBLIC_PATHS)

    def test_21_generate_key(self):
        import os
        os.environ.pop("WAGGLE_API_KEY", None)
        from backend.auth import get_or_create_api_key
        # Use temp dir so we don't modify real .env
        key = get_or_create_api_key(env_path="/tmp/waggle_test_env_nonexistent")
        self.assertTrue(len(key) > 20)
        # Should be set in env now
        self.assertEqual(os.environ.get("WAGGLE_API_KEY"), key)
        # Cleanup
        os.environ.pop("WAGGLE_API_KEY", None)

    def test_22_public_paths_exempt(self):
        from backend.auth import PUBLIC_PATHS
        self.assertIn("/health", PUBLIC_PATHS)
        self.assertIn("/ready", PUBLIC_PATHS)
        self.assertIn("/api/status", PUBLIC_PATHS)
        self.assertNotIn("/api/chat", PUBLIC_PATHS)


class TestCollectTrainingData(unittest.TestCase):
    """Tests for tools/collect_training_data.py."""

    def test_30_import(self):
        from tools.collect_training_data import (
            collect_from_finetune_live, collect_from_curated,
            export_pairs, show_stats
        )

    def test_31_collect_empty_dir(self):
        from tools.collect_training_data import collect_from_finetune_live
        with tempfile.TemporaryDirectory() as d:
            pairs = collect_from_finetune_live(Path(d))
            self.assertEqual(len(pairs), 0)

    def test_32_collect_from_jsonl(self):
        from tools.collect_training_data import collect_from_finetune_live
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "finetune_live.jsonl"
            with open(p, "w", encoding="utf-8") as f:
                entry = {
                    "messages": [
                        {"role": "system", "content": "System"},
                        {"role": "user", "content": "Mikä on varroapunkki?"},
                        {"role": "assistant", "content": "Varroapunkki (Varroa destructor) on mehiläisten ulkoloinen, joka aiheuttaa merkittävää tuhoa mehiläisyhdyskunnissa."},
                    ],
                    "quality_score": 9,
                }
                f.write(json.dumps(entry) + "\n")
            pairs = collect_from_finetune_live(Path(d))
            self.assertEqual(len(pairs), 1)
            self.assertEqual(pairs[0]["source"], "finetune_live")

    def test_33_filters_errors(self):
        from tools.collect_training_data import collect_from_finetune_live
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "finetune_live.jsonl"
            with open(p, "w", encoding="utf-8") as f:
                entry = {
                    "messages": [
                        {"role": "user", "content": "test question"},
                        {"role": "assistant", "content": "Error occurred while processing this failed request timeout"},
                    ],
                    "quality_score": 8,
                }
                f.write(json.dumps(entry) + "\n")
            pairs = collect_from_finetune_live(Path(d))
            self.assertEqual(len(pairs), 0)

    def test_34_export_pairs(self):
        from tools.collect_training_data import export_pairs
        with tempfile.TemporaryDirectory() as d:
            pairs = [
                {"question": "Q1", "answer": "A1", "source": "test", "confidence": 0.9},
                {"question": "Q2", "answer": "A2", "source": "test", "confidence": 0.8},
            ]
            out = Path(d) / "out.jsonl"
            export_pairs(pairs, out)
            self.assertTrue(out.exists())
            lines = out.read_text(encoding="utf-8").strip().split("\n")
            self.assertEqual(len(lines), 2)


class TestTrainScript(unittest.TestCase):
    """Tests for tools/train_micromodel_v3.py."""

    def test_40_import(self):
        from tools.train_micromodel_v3 import (
            check_dependencies, load_training_data, DEFAULT_BASE_MODEL
        )

    def test_41_check_dependencies(self):
        from tools.train_micromodel_v3 import check_dependencies
        deps = check_dependencies()
        self.assertIn("torch", deps)
        self.assertIn("peft", deps)
        self.assertIn("unsloth", deps)

    def test_42_load_empty(self):
        from tools.train_micromodel_v3 import load_training_data
        with tempfile.TemporaryDirectory() as d:
            data = load_training_data(Path(d) / "nonexistent.jsonl")
            self.assertEqual(len(data), 0)

    def test_43_load_valid_data(self):
        from tools.train_micromodel_v3 import load_training_data
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "test.jsonl"
            with open(p, "w", encoding="utf-8") as f:
                for i in range(10):
                    f.write(json.dumps({
                        "messages": [
                            {"role": "user", "content": f"Q{i}"},
                            {"role": "assistant", "content": f"A{i}"},
                        ],
                        "confidence": 0.9,
                    }) + "\n")
            data = load_training_data(p)
            self.assertEqual(len(data), 10)


if __name__ == "__main__":
    unittest.main()
