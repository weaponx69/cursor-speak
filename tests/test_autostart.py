import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cursor_speak import autostart, config  # noqa: E402


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.user = root / "user" / "cursor-speak.desktop"
        self.system = root / "system" / "cursor-speak.desktop"
        self.patches = [
            mock.patch.object(config, "AUTOSTART_FILE", self.user),
            mock.patch.object(autostart, "SYSTEM_FILE", self.system),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_deb_default_on_and_override_off(self):
        self.system.parent.mkdir()
        self.system.write_text("[Desktop Entry]\n")
        self.assertTrue(autostart.is_enabled())
        autostart.set_enabled(False)
        self.assertFalse(autostart.is_enabled())
        self.assertIn("Hidden=true", self.user.read_text())
        autostart.set_enabled(True)
        self.assertTrue(autostart.is_enabled())
        self.assertFalse(self.user.exists())

    def test_appimage_default_off_and_launches_the_appimage(self):
        self.assertFalse(autostart.is_enabled())
        with mock.patch.dict(os.environ, {"APPIMAGE": "/opt/apps/Cursor_Speak.AppImage"}):
            autostart.set_enabled(True)
        text = self.user.read_text()
        self.assertIn('Exec="/opt/apps/Cursor_Speak.AppImage" --background', text)
        self.assertTrue(autostart.is_enabled())
        autostart.set_enabled(False)
        self.assertFalse(autostart.is_enabled())
        self.assertFalse(self.user.exists())

    def test_source_checkout_uses_plain_command(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("APPIMAGE", None)
            autostart.set_enabled(True)
        self.assertIn("Exec=cursor-speak --background", self.user.read_text())


if __name__ == "__main__":
    unittest.main()
