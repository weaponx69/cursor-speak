import os
import unittest
from pathlib import Path
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cursor_speak import setup_voice  # noqa: E402


class SetupVoiceTests(unittest.TestCase):
    def test_pip_install_fails_fast(self):
        cmd = setup_voice.pip_install_cmd("/tmp/venv/bin/python")
        self.assertEqual(cmd[0], "/tmp/venv/bin/python")
        self.assertIn("piper-tts", cmd)
        i = cmd.index("--retries")
        self.assertEqual(cmd[i + 1], "0")
        i = cmd.index("--timeout")
        self.assertEqual(cmd[i + 1], "15")

    def test_host_python_skips_appdir_binary(self):
        with mock.patch.dict(os.environ, {"APPDIR": "/tmp/mount.AppDir", "CURSOR_SPEAK_HOST_PYTHON": ""}, clear=False):
            os.environ.pop("CURSOR_SPEAK_HOST_PYTHON", None)
            with mock.patch.object(setup_voice.shutil, "which", return_value="/tmp/mount.AppDir/usr/bin/python3"):
                with mock.patch.object(setup_voice.sys, "executable", "/usr/bin/python3"):
                    with mock.patch.object(setup_voice.Path, "is_file", return_value=True):
                        self.assertEqual(setup_voice.host_python(), "/usr/bin/python3")


if __name__ == "__main__":
    unittest.main()
