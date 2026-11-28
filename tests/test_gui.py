"""Builds the GTK windows. Runs only with CURSOR_SPEAK_GUI_TESTS=1 (CI, under
xvfb), so a developer's desktop never gets test windows."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

GUI = os.environ.get("CURSOR_SPEAK_GUI_TESTS") == "1"


@unittest.skipUnless(GUI, "set CURSOR_SPEAK_GUI_TESTS=1 under xvfb")
class ConversationWindowTests(unittest.TestCase):
    def test_window_shows_transcript_and_state(self):
        from cursor_speak import converse_ui

        conv = mock.Mock()
        ui, GLib, Gtk = converse_ui._gtk_window({"conv": conv}, "/tmp")
        ui._state("thinking", "")
        ui._line("you", "run the tests")
        ui._line("tool", "$ npm test")
        ui._line("agent", "All tests pass.")
        while Gtk.events_pending():
            Gtk.main_iteration()
        buf = ui.buf
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        self.assertIn("You: run the tests", text)
        self.assertIn("› $ npm test", text)
        self.assertIn("All tests pass.", text)
        self.assertIn("Agent is working", ui.status.get_text())
        ui.entry.set_text("typed message")
        ui._typed()
        conv.handle_text.assert_called_with("typed message")
        ui.pause_btn.clicked()
        conv.toggle_pause.assert_called_once()
        ui.w.destroy()

    def test_main_window_has_voice_section(self):
        from cursor_speak import config, tray

        ctl = mock.Mock()
        ctl.cfg = dict(config.DEFAULTS)
        ctl.paused = False
        ctl.listening = None
        ctl.status_line.return_value = "Listening to 0 chats"
        ctl.speaker.backend = "none"
        with mock.patch.object(tray, "tray_host_available", return_value=False):
            app = tray.TrayApp(ctl, force_window=True)
        self.assertEqual(app.win_talk.get_label(), "Talk")
        self.assertIn("Push-to-talk", app._voice_note())
        app.window.destroy()


if __name__ == "__main__":
    unittest.main()
