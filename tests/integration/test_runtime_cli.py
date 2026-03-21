"""CLI argument parsing tests for start_runtime.py.

Tests R-1, R-2, R-5 acceptance criteria from WORK_ORDER.md.
"""

import unittest

from waggledance.adapters.cli.start_runtime import parse_args


class TestParseArgs(unittest.TestCase):
    """Tests for parse_args() extracted function."""

    def test_default_values(self):
        """No arguments -> host=0.0.0.0, port=8000, stub=False, log_level=warning."""
        args = parse_args([])
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 8000)
        self.assertFalse(args.stub)
        self.assertEqual(args.log_level, "warning")

    def test_parse_stub(self):
        """--stub -> stub=True."""
        args = parse_args(["--stub"])
        self.assertTrue(args.stub)

    def test_parse_host_port(self):
        """--host 127.0.0.1 --port 9000 -> correct values."""
        args = parse_args(["--host", "127.0.0.1", "--port", "9000"])
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 9000)

    def test_parse_log_level(self):
        """--log-level debug -> 'debug'."""
        args = parse_args(["--log-level", "debug"])
        self.assertEqual(args.log_level, "debug")

    def test_parse_log_level_info(self):
        """--log-level info -> 'info'."""
        args = parse_args(["--log-level", "info"])
        self.assertEqual(args.log_level, "info")

    def test_all_args_combined(self):
        """All arguments together."""
        args = parse_args([
            "--stub",
            "--host", "localhost",
            "--port", "3000",
            "--log-level", "error",
        ])
        self.assertTrue(args.stub)
        self.assertEqual(args.host, "localhost")
        self.assertEqual(args.port, 3000)
        self.assertEqual(args.log_level, "error")

    def test_port_is_integer(self):
        """Port should be parsed as int, not str."""
        args = parse_args(["--port", "4567"])
        self.assertIsInstance(args.port, int)

    def test_invalid_log_level_rejected(self):
        """Invalid log level raises SystemExit."""
        with self.assertRaises(SystemExit):
            parse_args(["--log-level", "verbose"])


if __name__ == "__main__":
    unittest.main()
