import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cursor_speak.cli import build_parser  # noqa: E402
from cursor_speak.tray import hide_window_on_close  # noqa: E402


class CliParseTests(unittest.TestCase):
    def setUp(self):
        self.p = build_parser()

    def test_default_is_run_with_a_window(self):
        ns = self.p.parse_args([])
        self.assertIsNone(ns.cmd)
        self.assertFalse(ns.background)
        self.assertFalse(ns.headless)
        self.assertFalse(ns.window)

    def test_background_aliases(self):
        self.assertTrue(self.p.parse_args(["--background"]).background)
        self.assertTrue(self.p.parse_args(["--hidden"]).background)
        ns = self.p.parse_args(["run", "--hidden", "-v"])
        self.assertEqual(ns.cmd, "run")
        self.assertTrue(ns.background)
        self.assertTrue(ns.verbose)

    def test_subcommands_still_work(self):
        self.assertEqual(self.p.parse_args(["status"]).cmd, "status")
        self.assertEqual(self.p.parse_args(["pause"]).cmd, "pause")
        ns = self.p.parse_args(["say", "hello", "world"])
        self.assertEqual(ns.cmd, "say")
        self.assertEqual(ns.text, ["hello", "world"])


class WindowCloseTests(unittest.TestCase):
    def test_close_hides_when_tray_exists(self):
        self.assertTrue(hide_window_on_close(True))
        self.assertFalse(hide_window_on_close(False))


if __name__ == "__main__":
    unittest.main()
